"""Primary-gate rescue pass -- F1.5.

Conditional rescue pass for rows where F1's deterministic primary gate fired.
Rows already excluded by the Stage 3 climate screen are auto-dropped. The
remaining flagged rows can be sent to an LM that uses the same climate-screen
rubric as Stage 3c to decide keep / drop / reclassify.
"""

from __future__ import annotations

from typing import Any, List, Literal, Optional

import dspy
from pydantic import BaseModel, Field

from .category_gates import apply_primary_gate_to_record, apply_secondary_gate_to_record
from .consistent_classification import CATEGORY_DEFINITIONS, CLIMATE_SCREEN_GUIDE


PrimaryCategory = Literal[
    "Mitigation",
    "Adaptation",
    "Resource Efficiency",
    "Nature-Based Solutions",
]


class PrimaryGateRescueResult(BaseModel):
    """Structured rescue verdict."""

    final_disposition: Literal["keep", "drop", "reclassify"] = Field(
        description=(
            "'keep' means the deterministic gate was too strict. 'drop' means "
            "the row should leave the climate dataset. 'reclassify' means the "
            "row is climate-relevant but the assigned primary category is wrong."
        )
    )
    suggested_primary: Optional[PrimaryCategory] = Field(
        default=None,
        description="Required when final_disposition == 'reclassify'. None otherwise.",
    )
    confidence: float = Field(description="0.0-1.0 confidence in the disposition.")
    reasoning: str = Field(
        description=(
            "Step-by-step rationale citing source quotes from policy_statement "
            "or verbatim_text."
        )
    )


class PrimaryGateRescueSignature(dspy.Signature):
    f"""You are reviewing a single climate policy record that failed a
    deterministic primary-category gate. Decide whether the row belongs in
    the climate dataset, and if so whether the primary_category is right.

    Use the same screening rubric the rest of the pipeline uses:

    {CLIMATE_SCREEN_GUIDE}

    Primary categories (when reclassifying):

    {CATEGORY_DEFINITIONS}

    DECISION RULES:
    - 'keep' -> policy_statement OR verbatim_text contains an explicit climate
      intervention or hazard, AND the assigned primary_category is consistent
      with that intervention.
    - 'reclassify' -> policy_statement OR verbatim_text contains an explicit
      climate intervention but the primary category is wrong.
    - 'drop' -> climate relevance is only indirect, inferred, speculative, or
      socially generic; OR canonical_mechanism is an explicit non-climate flag
      such as housing_first, no_direct_climate_mechanism, or
      inclusive_public_engagement.
    """

    policy_statement: str = dspy.InputField()
    verbatim_text: str = dspy.InputField()
    parent_statement: str = dspy.InputField(default="None")
    role: str = dspy.InputField(default="individual")
    primary_category: str = dspy.InputField()
    canonical_mechanism: str = dspy.InputField()
    gate_failure_reason: str = dspy.InputField()
    climate_screen: str = dspy.InputField(
        desc=(
            "Stage 3c verdict: explicit_self / explicit_parent / exclude. "
            "When this disagrees with the primary gate, break the tie."
        )
    )

    rescue: PrimaryGateRescueResult = dspy.OutputField()


class PrimaryGateRescuer(dspy.Module):
    def __init__(self):
        super().__init__()
        self.rescue = dspy.ChainOfThought(PrimaryGateRescueSignature)

    def forward(self, **kwargs):
        return self.rescue(**kwargs)


def _is_unset(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, float) and value != value)


def _is_gate_false(value: Any) -> bool:
    if value is False:
        return True
    return str(value).strip().lower() in {"false", "0", "no"}


def _as_rescue_result(raw: Any) -> PrimaryGateRescueResult:
    if isinstance(raw, PrimaryGateRescueResult):
        return raw
    if isinstance(raw, dict):
        return PrimaryGateRescueResult(**raw)
    if hasattr(raw, "model_dump"):
        return PrimaryGateRescueResult(**raw.model_dump())
    data = {
        "final_disposition": getattr(raw, "final_disposition", None),
        "suggested_primary": getattr(raw, "suggested_primary", None),
        "confidence": getattr(raw, "confidence", 0.0),
        "reasoning": getattr(raw, "reasoning", ""),
    }
    return PrimaryGateRescueResult(**data)


def _mark_keep(record: dict, *, reasoning: str, confidence: float) -> None:
    record["rescue_disposition"] = "keep"
    record["rescue_reasoning"] = reasoning
    record["rescue_confidence"] = float(confidence)
    record["primary_gate_rescued"] = True
    record["primary_gate_passed"] = True
    record["primary_gate_reason"] = "rescued_by_f1_5"
    record.setdefault("dropped_by_climate_rescue", False)


def _mark_drop(record: dict, *, reasoning: str, confidence: float) -> None:
    record["rescue_disposition"] = "drop"
    record["rescue_reasoning"] = reasoning
    record["rescue_confidence"] = float(confidence)
    record["dropped_by_climate_rescue"] = True


def _mark_reclassify(
    record: dict,
    *,
    suggested_primary: str,
    reasoning: str,
    confidence: float,
    rerun_f4: bool,
) -> None:
    record["rescue_disposition"] = "reclassify"
    record["rescue_reasoning"] = reasoning
    record["rescue_confidence"] = float(confidence)
    record["primary_category_original"] = record.get("primary_category")
    record["primary_category"] = suggested_primary
    record["primary_category_rescue_corrected"] = True
    record.setdefault("dropped_by_climate_rescue", False)
    apply_primary_gate_to_record(record)
    if rerun_f4:
        apply_secondary_gate_to_record(record)


def apply_primary_gate_rescue_dry(records: List[dict]) -> List[dict]:
    """Dry-run rescue: auto-drop climate_screen=exclude, keep all other F1 fails."""
    for record in records:
        if not _is_gate_false(record.get("primary_gate_passed")):
            continue
        if not _is_unset(record.get("rescue_disposition")):
            continue
        if str(record.get("climate_screen", "")).strip().lower() == "exclude":
            _mark_drop(record, reasoning="dry-run auto-drop", confidence=1.0)
        else:
            _mark_keep(record, reasoning="dry-run auto-keep", confidence=1.0)
    return records


def apply_primary_gate_rescue(
    records: List[dict],
    *,
    rescuer: Optional[PrimaryGateRescuer] = None,
    parallel: bool = True,
    num_threads: int = 8,
    rerun_f4_on_reclassify: bool = True,
) -> List[dict]:
    """Apply F1.5 rescue over a list of records. Mutates in place.

    Idempotent: rows with a populated rescue_disposition are skipped.
    """
    candidates = [
        r
        for r in records
        if _is_gate_false(r.get("primary_gate_passed"))
        and _is_unset(r.get("rescue_disposition"))
    ]
    if not candidates:
        return records

    for record in candidates:
        if str(record.get("climate_screen", "")).strip().lower() == "exclude":
            _mark_drop(
                record,
                reasoning="auto: climate_screen=exclude",
                confidence=1.0,
            )

    needs_lm = [
        r
        for r in candidates
        if str(r.get("climate_screen", "")).strip().lower() != "exclude"
        and _is_unset(r.get("rescue_disposition"))
    ]
    if not needs_lm:
        return records

    rescuer = rescuer or PrimaryGateRescuer()

    def _rescue_one(record: dict) -> dict:
        try:
            prediction = rescuer(
                policy_statement=record.get("policy_statement", ""),
                verbatim_text=record.get("verbatim_text", ""),
                parent_statement=record.get("parent_statement") or "None",
                role=record.get("role", "individual"),
                primary_category=record.get("primary_category", ""),
                canonical_mechanism=record.get("canonical_mechanism", ""),
                gate_failure_reason=record.get("primary_gate_reason", ""),
                climate_screen=record.get("climate_screen", "unknown"),
            )
            verdict = _as_rescue_result(getattr(prediction, "rescue", prediction))
        except Exception as exc:
            _mark_keep(
                record,
                reasoning=f"LM error: {exc!r}",
                confidence=0.0,
            )
            return record

        disposition = verdict.final_disposition
        if disposition == "keep":
            _mark_keep(
                record,
                reasoning=verdict.reasoning,
                confidence=verdict.confidence,
            )
        elif disposition == "drop":
            _mark_drop(
                record,
                reasoning=verdict.reasoning,
                confidence=verdict.confidence,
            )
        elif disposition == "reclassify":
            if verdict.suggested_primary:
                _mark_reclassify(
                    record,
                    suggested_primary=verdict.suggested_primary,
                    reasoning=verdict.reasoning,
                    confidence=verdict.confidence,
                    rerun_f4=rerun_f4_on_reclassify,
                )
            else:
                _mark_keep(
                    record,
                    reasoning=(
                        "LM returned reclassify without suggested_primary; "
                        f"fail-safe keep. Original reasoning: {verdict.reasoning}"
                    ),
                    confidence=0.0,
                )
        return record

    if parallel and len(needs_lm) > 1:
        try:
            from dspy.utils.parallelizer import ParallelExecutor

            executor = ParallelExecutor(
                num_threads=max(1, min(num_threads, len(needs_lm))),
                max_errors=len(needs_lm),
                provide_traceback=True,
            )
            executor.execute(_rescue_one, needs_lm)
        except ImportError:
            for record in needs_lm:
                _rescue_one(record)
    else:
        for record in needs_lm:
            _rescue_one(record)

    return records


def summarize_rescue(records: List[dict]) -> dict:
    """Return run-level counts for rescue logging."""
    counts = {
        "keep": 0,
        "drop_lm": 0,
        "drop_auto": 0,
        "reclassify": 0,
        "untouched": 0,
    }
    for record in records:
        disposition = record.get("rescue_disposition")
        if _is_unset(disposition):
            counts["untouched"] += 1
        elif disposition == "keep":
            counts["keep"] += 1
        elif disposition == "drop":
            reason = str(record.get("rescue_reasoning", ""))
            if reason.startswith(("auto:", "dry-run auto-drop")):
                counts["drop_auto"] += 1
            else:
                counts["drop_lm"] += 1
        elif disposition == "reclassify":
            counts["reclassify"] += 1
    return counts
