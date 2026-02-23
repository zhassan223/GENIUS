# ═══════════════════════════════════════════════════════════════════════════════
# INITIATIVE-LEVEL VALIDATION MODULE
# ═══════════════════════════════════════════════════════════════════════════════

"""
Purpose:
    Evaluate parent+sub clusters as whole INITIATIVES / ACTION PLANS,
    rather than grading each sub-action in isolation.

Design philosophy — LENIENT for initiatives:
    • Sub-actions INHERIT parent-level context (binding mechanism, spatial
      scope, sector framing) — they don't need to repeat it.
    • A weak individual sub (e.g. "explore partnerships") is acceptable
      if the cluster as a whole has strong anchors.
    • Three-tier verdict: SOUND / PARTIAL / WEAK  (not binary).
    • Confidence floors are softer than the individual-policy validator.
"""

# ─── Imports ────────────────────────────────────────────────────────────────

from typing import List, Optional, Literal
from pydantic import BaseModel, Field
import dspy
import pandas as pd
from tqdm import tqdm


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  COMPOSITE INPUT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_initiative_context(cluster: dict) -> dict:
    """
    Assemble a single evaluation payload from a parent_with_subs cluster.

    Returns a dict ready to feed into the InitiativeValidator.
    """
    parent = cluster["parent"]
    subs   = cluster["subs"]

    # ── Combined verbatim block (preserves all original evidence) ────────
    combined_verbatim = (
        f"[PARENT / INITIATIVE HEADER]\n"
        f"{parent.verbatim_text}\n"
    )
    for i, sub in enumerate(subs, 1):
        combined_verbatim += (
            f"\n[SUB-ACTION {i}]\n"
            f"{sub.verbatim_text}\n"
        )

    # ── Structured sub-action summaries ──────────────────────────────────
    sub_summaries = []
    for i, sub in enumerate(subs, 1):
        sub_summaries.append(
            f"Action {i}: {sub.policy_statement}"
        )

    return {
        # Identity
        "initiative_name":    parent.section_header,
        "parent_statement":   parent.policy_statement,
        "sector":             parent.sector,
        "extraction_rationale": parent.extraction_rationale,

        # Content for the LLM
        "sub_action_summaries": "\n".join(sub_summaries),
        "combined_verbatim":    combined_verbatim,
        "num_sub_actions":      len(subs),

        # Pass-through for record-keeping
        "_cluster_obj": cluster,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  PYDANTIC OUTPUT SCHEMA  —  InitiativeMetrics
# ═══════════════════════════════════════════════════════════════════════════════

YesNo = Literal["Yes", "No"]


class SubActionAssessment(BaseModel):
    """Quick per-sub-action assessment (embedded in initiative output)."""

    action_label: str = Field(
        description="Short identifier, e.g. 'Action 1: Retrofit residential ≤4 units'."
    )
    has_quantifiable_target: YesNo = Field(
        description=(
            "'Yes' ONLY if this sub-action's verbatim text contains an explicit "
            "measurable OUTCOME (%, MW, units, tonnes, etc.). "
            "Budget figures alone do NOT qualify."
        )
    )
    has_timeline: YesNo = Field(
        description=(
            "'Yes' ONLY if the sub-action specifies an explicit deadline or year."
        )
    )
    is_concrete_action: YesNo = Field(
        description=(
            "'Yes' if the sub-action describes a tangible deliverable or intervention "
            "(retrofit, install, mandate, ban, build, electrify). "
            "'No' for process-only items (study, explore, promote, raise awareness)."
        )
    )
    strength: Literal["strong", "moderate", "weak"] = Field(
        description=(
            "'strong'  = has target + timeline + concrete action. "
            "'moderate' = missing one of the three but still contributes meaningfully. "
            "'weak'    = aspirational / process-only / no measurable anchor."
        )
    )


class InitiativeMetrics(BaseModel):
    """
    Initiative-level (cluster-level) evaluation output.

    This is intentionally MORE LENIENT than the individual PolicyValidator:
    sub-actions inherit parent context, and the cluster is judged holistically.
    """

    # ── A. Aggregate criteria ────────────────────────────────────────────

    coverage_assessment: str = Field(
        description=(
            "In 2-3 sentences: Do the sub-actions COLLECTIVELY cover the parent's "
            "stated goal?  Identify any obvious gaps (e.g., the parent says "
            "'all building types' but subs only cover residential)."
        )
    )
    coverage_score: float = Field(
        description=(
            "0.0–1.0.  1.0 = sub-actions fully address the parent goal with no "
            "obvious gaps.  0.5 = partial coverage, notable omissions.  "
            "< 0.3 = sub-actions barely relate to the parent goal."
        )
    )

    coherence_assessment: str = Field(
        description=(
            "In 2-3 sentences: Are the sub-actions complementary and logically "
            "ordered?  Flag redundancies, contradictions, or timeline conflicts."
        )
    )
    coherence_score: float = Field(
        description=(
            "0.0–1.0.  1.0 = sub-actions are complementary and non-redundant. "
            "0.5 = some overlap or minor logical issues. "
            "< 0.3 = contradictory or incoherent."
        )
    )

    aggregate_measurability: YesNo = Field(
        description=(
            "'Yes' if the initiative AS A WHOLE contains enough quantifiable targets "
            "and timelines to track progress — even if not every sub-action has its "
            "own numbers.  LENIENT: a cluster with 3/5 subs having targets is 'Yes'."
        )
    )

    has_implementation_pathway: YesNo = Field(
        description=(
            "'Yes' if the sub-actions TOGETHER form a plausible delivery chain: "
            "they address different facets (policy + program + target), name "
            "responsible actors, or sequence logically.  "
            "LENIENT: even a rough pathway counts."
        )
    )

    # ── B. Inherited / parent-level attributes ───────────────────────────

    inherited_binding_mechanism: YesNo = Field(
        description=(
            "'Yes' if the PARENT initiative text indicates enforceable authority "
            "(ordinance, regulation, mandate, code change, council approval) that "
            "would apply to all sub-actions beneath it.  Sub-actions inherit this."
        )
    )
    inherited_spatial_scope: YesNo = Field(
        description=(
            "'Yes' if the PARENT text establishes a geographic or institutional "
            "scope (citywide, specific neighborhoods, City-owned assets) that "
            "sub-actions inherit."
        )
    )

    # ── C. Per-sub-action assessments ────────────────────────────────────

    sub_assessments: List[SubActionAssessment] = Field(
        description=(
            "One assessment per sub-action, in order.  Use the sub-action's own "
            "verbatim text as evidence.  Be fair: if a sub is vague but the parent "
            "supplies the missing context (scope, mechanism), note that."
        )
    )

    # ── D. Signal flags (initiative-level) ───────────────────────────────

    weak_signals: str = Field(
        description=(
            "Comma-separated initiative-level red flags found across the combined "
            "verbatim text (e.g., 'promote, explore, awareness, no deadlines for "
            "3 of 5 subs').  Write 'None' if none found."
        )
    )
    strong_signals: str = Field(
        description=(
            "Comma-separated initiative-level strength markers found across the "
            "combined verbatim text (e.g., '4 of 5 subs have numeric targets, "
            "mandatory retrofit, 62% by 2040').  Write 'None' if none found."
        )
    )

    # ── E. Verdicts ──────────────────────────────────────────────────────

    initiative_result: Literal["SOUND", "PARTIAL", "WEAK"] = Field(
        description=(
            "Apply the INITIATIVE SOUNDNESS RULE (see signature docstring). "
            "'SOUND'   = initiative is well-structured, measurable, and actionable. "
            "'PARTIAL' = meaningful but has notable gaps or weak sub-actions. "
            "'WEAK'    = mostly aspirational, incoherent, or unmeasurable."
        )
    )
    confidence_score: float = Field(
        description=(
            "0.0–1.0 confidence in the initiative_result. "
            "LENIENT CAPS (softer than individual validation): "
            "If coverage_score < 0.4 → max 0.5. "
            "If > half of sub_assessments are 'weak' → max 0.6. "
            "Otherwise calibrate based on aggregate evidence strength."
        )
    )

    initiative_reasoning: str = Field(
        description=(
            "Step-by-step justification.  MUST: "
            "(1) Summarize what the initiative is trying to achieve. "
            "(2) Note how many subs are strong / moderate / weak. "
            "(3) State whether the parent supplies binding/scope context that "
            "    lifts weaker subs. "
            "(4) Explain coverage and coherence scores. "
            "(5) Apply the INITIATIVE SOUNDNESS RULE and state verdict."
        )
    )

    final_verdict: bool = Field(
        description=(
            "True if the initiative is worth keeping as an actionable plan. "
            "LENIENT RULE: True if initiative_result in ('SOUND', 'PARTIAL') "
            "AND confidence_score >= 0.55.  "
            "This is deliberately lower than the 0.75/0.8 threshold used for "
            "individual policies, because initiatives derive strength from "
            "their cluster structure."
        )
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  DSPy SIGNATURE  —  InitiativeValidationSignature
# ═══════════════════════════════════════════════════════════════════════════════

class InitiativeValidationSignature(dspy.Signature):
    """
    Evaluate whether a CLUSTER of related climate policies (an initiative /
    action plan consisting of a parent policy and its sub-actions) is SOUND,
    PARTIAL, or WEAK as a whole.

    ═══════════════════════════════════════════════════════════════════════
    KEY PHILOSOPHY — BE LENIENT FOR INITIATIVES
    ═══════════════════════════════════════════════════════════════════════

    Unlike individual policy validation, initiative evaluation recognizes that:

    • Sub-actions INHERIT context from the parent.  If the parent states
      "under Ordinance X" or "citywide", every sub inherits that binding
      mechanism or spatial scope — do NOT penalize subs for not repeating it.

    • A single weak sub-action does NOT invalidate an otherwise strong
      initiative.  Real-world action plans commonly include a mix of hard
      targets and softer enabling actions.

    • Coverage and coherence matter MORE than whether each sub has all
      five individual criteria.  A well-structured plan with 4 strong subs
      and 1 vague one is still a SOUND initiative.

    • Process / enabling actions (e.g., "update land-use policies by 2023")
      are acceptable within a cluster IF they support the delivery of
      measurable sibling actions.

    ═══════════════════════════════════════════════════════════════════════
    INITIATIVE SOUNDNESS RULE
    ═══════════════════════════════════════════════════════════════════════

    SOUND — ALL of the following:
      (a) coverage_score >= 0.6
      (b) coherence_score >= 0.5
      (c) aggregate_measurability = 'Yes'
      (d) At least HALF of sub_assessments are 'strong' or 'moderate'
      (e) At least ONE sub_assessment is 'strong'

    PARTIAL — does NOT meet all SOUND conditions, but:
      (a) coverage_score >= 0.4
      (b) At least ONE sub_assessment is 'strong' or 'moderate'
      (c) The initiative is not purely aspirational — there is at least
          one measurable target or deadline somewhere in the cluster

    WEAK — everything else:
      • Mostly aspirational language across the cluster
      • No measurable targets or all subs are 'weak'
      • Incoherent (subs don't relate to parent goal)

    ═══════════════════════════════════════════════════════════════════════
    CONFIDENCE CAPS (lenient)
    ═══════════════════════════════════════════════════════════════════════

    • coverage_score < 0.4 → confidence ≤ 0.5
    • More than half of subs are 'weak' → confidence ≤ 0.6
    • All subs are 'strong' AND coverage_score >= 0.8 → confidence >= 0.85

    ═══════════════════════════════════════════════════════════════════════
    INHERITANCE RULES
    ═══════════════════════════════════════════════════════════════════════

    When evaluating each sub-action, consider what the PARENT provides:

    1. BINDING MECHANISM: If the parent references an ordinance, council
       approval, or regulatory authority, ALL subs inherit this.  A sub
       that says "retrofit 20% of buildings by 2030" without mentioning
       the ordinance is still binding if the parent establishes it.

    2. SPATIAL SCOPE: If the parent says "citywide" or names specific
       neighborhoods, subs inherit that geographic scope.

    3. SECTOR CONTEXT: The parent's sector framing applies to all subs.

    4. IMPLEMENTATION PATHWAY: The set of subs together may constitute
       a pathway even if no single sub describes the full chain.

    ═══════════════════════════════════════════════════════════════════════
    BUDGET & FUNDING IN INITIATIVES
    ═══════════════════════════════════════════════════════════════════════

    A budget line-item sub (e.g., "$6M for retrofits") is ACCEPTABLE within
    an initiative if sibling subs supply the measurable outcomes and
    timelines.  The budget sub adds implementation credibility — do NOT
    penalize it.  Only flag budget subs as weak if they exist in isolation
    with no measurable siblings.

    ═══════════════════════════════════════════════════════════════════════
    FINAL VERDICT (lenient threshold)
    ═══════════════════════════════════════════════════════════════════════

    final_verdict = True if:
        initiative_result in ('SOUND', 'PARTIAL')
        AND confidence_score >= 0.55

    This is deliberately lower than the individual policy threshold (0.75+)
    because initiatives derive strength from cluster structure.
    """

    # ── Inputs ───────────────────────────────────────────────────────────

    initiative_name: str = dspy.InputField(
        desc="Name / section header of the initiative (from the parent policy)"
    )
    parent_statement: str = dspy.InputField(
        desc="The parent policy's concise summary statement"
    )
    sub_action_summaries: str = dspy.InputField(
        desc="Numbered list of all sub-action policy statements"
    )
    combined_verbatim: str = dspy.InputField(
        desc=(
            "Full verbatim text block: parent header text followed by each "
            "sub-action's original document text.  ALL evidence must come "
            "from this field."
        )
    )
    sector: str = dspy.InputField(
        desc="Primary climate sector for the initiative"
    )
    num_sub_actions: int = dspy.InputField(
        desc="Total number of sub-actions in this initiative"
    )

    # ── Output ───────────────────────────────────────────────────────────

    initiative_metrics: InitiativeMetrics = dspy.OutputField(
        desc="Full initiative-level evaluation with per-sub assessments and verdict"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  DSPy MODULE  —  InitiativeValidator
# ═══════════════════════════════════════════════════════════════════════════════

class InitiativeValidator(dspy.Module):
    """
    Evaluates parent+sub policy clusters as whole initiatives.

    Uses ChainOfThought for step-by-step reasoning before producing
    the structured InitiativeMetrics output.
    """

    def __init__(self):
        super().__init__()
        self.validate = dspy.ChainOfThought(InitiativeValidationSignature)

    def forward(self, initiative_data: dict):
        result = self.validate(
            initiative_name=initiative_data.get("initiative_name", ""),
            parent_statement=initiative_data.get("parent_statement", ""),
            sub_action_summaries=initiative_data.get("sub_action_summaries", ""),
            combined_verbatim=initiative_data.get("combined_verbatim", ""),
            sector=initiative_data.get("sector", "General"),
            num_sub_actions=initiative_data.get("num_sub_actions", 0),
        )
        return result


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  PIPELINE INTEGRATION  —  run_initiative_validation()
# ═══════════════════════════════════════════════════════════════════════════════

def run_initiative_validation(
    policy_clusters: list,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    Validate all parent_with_subs clusters as initiatives.

    Parameters
    ----------
    policy_clusters : list[dict]
        Output of cluster_policies() from the existing pipeline.
    verbose : bool
        Print summary statistics after validation.

    Returns
    -------
    pd.DataFrame
        One row per initiative with all metrics flattened.
    """
    # ── Filter to initiative clusters only ────────────────────────────────
    initiative_clusters = [
        c for c in policy_clusters
        if c["cluster_type"] == "parent_with_subs"
    ]

    if not initiative_clusters:
        print("⚠ No parent_with_subs clusters found. Nothing to validate.")
        return pd.DataFrame()

    # ── Run validation ───────────────────────────────────────────────────
    validator = InitiativeValidator()
    results = []

    for cluster in tqdm(initiative_clusters, desc="Validating initiatives"):
        context = build_initiative_context(cluster)

        try:
            prediction = validator(initiative_data=context)

            # Extract the InitiativeMetrics object
            metrics: InitiativeMetrics = prediction.initiative_metrics

            # Build a flat record
            record = {
                # Identity
                "initiative_name":      context["initiative_name"],
                "parent_statement":     context["parent_statement"],
                "sector":               context["sector"],
                "num_sub_actions":      context["num_sub_actions"],

                # Aggregate scores
                "coverage_score":       metrics.coverage_score,
                "coverage_assessment":  metrics.coverage_assessment,
                "coherence_score":      metrics.coherence_score,
                "coherence_assessment": metrics.coherence_assessment,
                "aggregate_measurability": metrics.aggregate_measurability,
                "has_implementation_pathway": metrics.has_implementation_pathway,

                # Inherited context
                "inherited_binding_mechanism": metrics.inherited_binding_mechanism,
                "inherited_spatial_scope":     metrics.inherited_spatial_scope,

                # Signals
                "weak_signals":   metrics.weak_signals,
                "strong_signals": metrics.strong_signals,

                # Verdicts
                "initiative_result":    metrics.initiative_result,
                "confidence_score":     metrics.confidence_score,
                "initiative_reasoning": metrics.initiative_reasoning,
                "final_verdict":        metrics.final_verdict,

                # Per-sub detail (stored as list of dicts for downstream use)
                "sub_assessments": [
                    sa.model_dump() if hasattr(sa, "model_dump") else sa.dict()
                    for sa in metrics.sub_assessments
                ],

                # Sub-action strength summary
                "subs_strong":   sum(1 for sa in metrics.sub_assessments if sa.strength == "strong"),
                "subs_moderate": sum(1 for sa in metrics.sub_assessments if sa.strength == "moderate"),
                "subs_weak":     sum(1 for sa in metrics.sub_assessments if sa.strength == "weak"),
            }
            results.append(record)

        except Exception as e:
            print(f"⚠ Error validating '{context['initiative_name']}': {e}")
            continue

    df_initiatives = pd.DataFrame(results)

    # ── Summary ──────────────────────────────────────────────────────────
    if verbose and len(df_initiatives) > 0:
        print(f"\n{'═' * 60}")
        print(f"  INITIATIVE VALIDATION SUMMARY")
        print(f"{'═' * 60}")
        print(f"  Total initiatives evaluated:  {len(df_initiatives)}")
        print(f"  SOUND:   {(df_initiatives['initiative_result'] == 'SOUND').sum()}")
        print(f"  PARTIAL: {(df_initiatives['initiative_result'] == 'PARTIAL').sum()}")
        print(f"  WEAK:    {(df_initiatives['initiative_result'] == 'WEAK').sum()}")
        print(f"  Final verdict = True:  {df_initiatives['final_verdict'].sum()}")
        print(f"  Avg confidence:        {df_initiatives['confidence_score'].mean():.2f}")
        print(f"  Avg coverage:          {df_initiatives['coverage_score'].mean():.2f}")
        print(f"  Avg coherence:         {df_initiatives['coherence_score'].mean():.2f}")
        print(f"{'═' * 60}\n")

    return df_initiatives


# ═══════════════════════════════════════════════════════════════════════════════
# 6.  COMBINED EXPORT HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def export_combined_results(
    df_individual_validated: pd.DataFrame,
    df_initiative_validated: pd.DataFrame,
    output_path: str = "full_validation_results.json",
) -> dict:
    """
    Combine individual and initiative validation into a single export.

    Returns a dict with both levels for downstream analysis.
    """
    import json

    combined = {
        "individual_policies": (
            df_individual_validated.to_dict(orient="records")
            if len(df_individual_validated) > 0 else []
        ),
        "initiatives": (
            df_initiative_validated.to_dict(orient="records")
            if len(df_initiative_validated) > 0 else []
        ),
        "summary": {
            "total_individual_policies": len(df_individual_validated),
            "total_initiatives": len(df_initiative_validated),
            "initiatives_sound": int(
                (df_initiative_validated["initiative_result"] == "SOUND").sum()
            ) if len(df_initiative_validated) > 0 else 0,
            "initiatives_partial": int(
                (df_initiative_validated["initiative_result"] == "PARTIAL").sum()
            ) if len(df_initiative_validated) > 0 else 0,
            "initiatives_weak": int(
                (df_initiative_validated["initiative_result"] == "WEAK").sum()
            ) if len(df_initiative_validated) > 0 else 0,
        },
    }

    with open(output_path, "w") as f:
        json.dump(combined, f, indent=2, default=str)

    print(f"✅ Combined results saved to {output_path}")
    return combined