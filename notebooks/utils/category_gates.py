"""
F1 + F4 — Category-evidence gates.

Two deterministic gates that run after the LM classification stages:

  PRIMARY GATE (F1)
    For each policy, check that the assigned primary_category has at least
    one category-appropriate outcome token in the SOURCE (policy_statement,
    verbatim_text, canonical_mechanism). Sources are checked, not the
    LM-generated mechanism_description, because the LM hallucinates framing
    to justify wrong tags (e.g. invents "ecosystem" language for brownfield
    redevelopment classified as NBS).
    The gate FLAGS but does NOT demote — there's nothing to demote a
    primary to without re-running Stage 2.

  SECONDARY / TYPOLOGY GATE (F4)
    For each row that has typology_code != 'None', check that:
      (a) typology_evidence_quote is a real substring of policy_statement
          OR verbatim_text (substring grounded), AND
      (b) strict pass: the quote contains BOTH required token groups for that
          subtype code (e.g. M-1 has efficiency + emissions tokens).
      (c) contextual pass for non-rare codes: the quote contains the secondary
          subtype token, while the wider row context contains the primary /
          climate token.
    If the gate fails, demote: typology_code='None', typology_evidence_quote
    ='None', confidence=0.0, secondary_categories='None'. Records the
    reason in `gate_demoted_reason` for traceability.

The token sets are intentionally inclusive of common synonyms so legitimate
clean rows (per Norhan's grading) still pass. Rare/fragile cross-over codes
remain strict-only.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional, Tuple


# ---------------------------------------------------------------------------
# REQUIRED OUTCOME TOKENS  (used by the primary gate + as building blocks for
# the per-subtype secondary requirements below)
# ---------------------------------------------------------------------------

MITIGATION_TOKENS: Tuple[str, ...] = (
    # core emission/GHG vocabulary
    "ghg", "greenhouse gas", "greenhouse gases",
    "emission", "emissions",
    "carbon", "co2", "co₂", "ch4", "methane",
    "decarboniz", "decarbonis",
    # net-zero / zero-carbon framings
    "net zero", "net-zero", "net_zero", "zero carbon", "zero-carbon",
    # fossil / sequestration / storage
    "fossil", "sequestration", "carbon sink", "carbon storage",
    "capture and storage",
    # supply-side mitigation tokens
    "renewable", "clean energy", "clean electricity",
    "low-carbon", "low carbon",
    "flaring",                # Qatar oil & gas
)

ADAPTATION_TOKENS: Tuple[str, ...] = (
    "hazard", "exposure", "vulnerab",
    "resilien",                # resilience, resilient, resiliency
    "adapt", "adaptive",
    "heat",                    # heat island, heatwave, etc.
    "flood", "flooding",
    "drought",
    "wildfire", "smoke",
    "storm", "storm surge",
    "sea level", "sea-level", "sea_level",
    "coastal",
    "stormwater",
    "preparedness",
    "early warning",
)

NBS_TOKENS: Tuple[str, ...] = (
    "ecosystem", "habitat",
    "tree", "trees", "canopy", "tree planting",
    "forest", "afforest", "reforest",
    "wetland", "wetlands", "riparian",
    "soil", "soil carbon",
    "bioswale", "bioswales",
    "green roof", "green roofs",
    "green infrastructure",
    "natural infrastructure",
    "nature based", "nature-based",
    "biodiversity",
    "regenerative",
)

RE_TOKENS: Tuple[str, ...] = (
    "efficien",                # efficiency, efficient
    "kwh", "kw·h",
    "per capita",
    "intensity",
    "benchmark", "benchmarking",
    "retrofit", "retrofits", "retrofitting",
    "weatheriz", "weatheris",
    "water conservation", "energy conservation",
    "consumption reduction", "reduce consumption",
    "epc",                     # London EPC building rating
    "energy performance",
    "energy management",
)

# Used by the primary gate.
REQUIRED_OUTCOME_TOKENS = {
    "Mitigation": MITIGATION_TOKENS,
    "Adaptation": ADAPTATION_TOKENS,
    "Nature-Based Solutions": NBS_TOKENS,
    "Resource Efficiency": RE_TOKENS,
}

# Mechanism strings the LM uses when it can't find a real climate connection.
# Any policy whose canonical_mechanism contains one of these should fail the
# primary gate regardless of the assigned category.
NON_CLIMATE_MECHANISM_FLAGS: Tuple[str, ...] = (
    "no_direct_climate_mechanism",
    "unspecified_climate_effect",
    "non_climate_economic_development",
    "poverty_reduction_goal",
    "housing_first",
    "affordable_housing_funding",
    "inclusive_public_engagement",   # LV 837: community engagement → Mitigation
    "displacement_prevention",
    "program_intervention_support",
)


# ---------------------------------------------------------------------------
# SUBTYPE REQUIREMENTS  (used by the F4 secondary gate)
# ---------------------------------------------------------------------------
# Each subtype code requires the evidence quote to contain at least one
# token from EACH of the listed groups.

SUBTYPE_REQUIREMENTS = {
    # Mitigation primary
    "M-1": (RE_TOKENS, MITIGATION_TOKENS),
    "M-2": (MITIGATION_TOKENS, ADAPTATION_TOKENS),
    "M-3": (NBS_TOKENS, MITIGATION_TOKENS),
    # Resource Efficiency primary
    "RE-1": (RE_TOKENS, MITIGATION_TOKENS),
    "RE-2": (RE_TOKENS, ADAPTATION_TOKENS),
    "RE-3": (RE_TOKENS, NBS_TOKENS),
    # Nature-Based Solutions primary
    "NBS-1": (NBS_TOKENS, ADAPTATION_TOKENS),
    "NBS-2": (NBS_TOKENS, MITIGATION_TOKENS),
    "NBS-3": (NBS_TOKENS, RE_TOKENS),
    # Adaptation primary
    "A-1": (ADAPTATION_TOKENS, NBS_TOKENS),
    "A-2": (ADAPTATION_TOKENS, RE_TOKENS),
    "A-3": (ADAPTATION_TOKENS, MITIGATION_TOKENS),
}

# For contextual F4 rescue: the quote itself must carry the secondary subtype
# signal, while the broader row can carry the primary/climate linkage.
SUBTYPE_SIGNAL_TOKENS = {
    "M-1": RE_TOKENS,
    "M-2": ADAPTATION_TOKENS,
    "M-3": NBS_TOKENS,
    "RE-1": MITIGATION_TOKENS,
    "RE-2": ADAPTATION_TOKENS,
    "RE-3": NBS_TOKENS,
    "NBS-1": ADAPTATION_TOKENS,
    "NBS-2": MITIGATION_TOKENS,
    "NBS-3": RE_TOKENS,
    "A-1": NBS_TOKENS,
    "A-2": RE_TOKENS,
    "A-3": MITIGATION_TOKENS,
}

PRIMARY_SIGNAL_TOKENS = {
    "M": MITIGATION_TOKENS,
    "RE": RE_TOKENS,
    "NBS": NBS_TOKENS,
    "A": ADAPTATION_TOKENS,
}

# Rare/fragile cross-over subtypes still require all evidence in the quote.
STRICT_ONLY_SUBTYPE_CODES = {"M-3", "RE-3", "NBS-3", "A-3"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _haystack(*parts: Optional[str]) -> str:
    parts = [str(p or "") for p in parts]
    text = " ".join(parts).lower()
    # Normalize: replace _-> space so 'energy_performance' and 'energy
    # performance' both match the 'energy performance' token.
    text = re.sub(r"[_]+", " ", text)
    return text


def _any_token_in(haystack: str, tokens: Iterable[str]) -> Optional[str]:
    """Return the first matching token, or None."""
    for tok in tokens:
        if tok and tok.lower() in haystack:
            return tok
    return None


# ---------------------------------------------------------------------------
# F1 PRIMARY GATE
# ---------------------------------------------------------------------------

def primary_passes_gate(
    primary_category: Optional[str],
    *,
    policy_statement: Optional[str] = "",
    verbatim_text: Optional[str] = "",
    canonical_mechanism: Optional[str] = "",
) -> Tuple[bool, str]:
    """F1 primary gate.

    Returns (passed, reason). The gate is intentionally lenient — its job
    is to flag the *clear* misclassifications (community engagement →
    Mitigation; brownfield redevelopment → NBS), not to second-guess
    every borderline case.

    Sources checked: policy_statement, verbatim_text, canonical_mechanism.
    The LM-generated mechanism_description is deliberately NOT used because
    the LM tends to invent supporting framing for wrong classifications.
    """
    if not primary_category:
        return True, "no primary category to check"

    # Bail-out: any 'no_direct_climate_mechanism'-style canonical mechanism
    # is an explicit non-climate signal regardless of category.
    cm_lower = (canonical_mechanism or "").lower()
    for flag in NON_CLIMATE_MECHANISM_FLAGS:
        if flag in cm_lower:
            return False, f"canonical_mechanism flagged as non-climate: {flag}"

    tokens = REQUIRED_OUTCOME_TOKENS.get(primary_category)
    if tokens is None:
        return True, f"no token set registered for category {primary_category!r}"

    hay = _haystack(policy_statement, verbatim_text, canonical_mechanism)
    hit = _any_token_in(hay, tokens)
    if hit is not None:
        return True, f"matched token: {hit!r}"
    return False, (
        f"no {primary_category} outcome token found in source; "
        f"flagging for review"
    )


# ---------------------------------------------------------------------------
# F4 SECONDARY / TYPOLOGY GATE
# ---------------------------------------------------------------------------

def is_substring_grounded(
    quote: Optional[str],
    *sources: Optional[str],
    min_chars: int = 12,
) -> bool:
    """Check that `quote` is a real verbatim span from any of the sources.

    Normalises whitespace + lowercases before comparing. Rejects very short
    quotes as insufficient evidence. Returns False for None/empty/'None'.
    """
    if not quote:
        return False
    q = re.sub(r"\s+", " ", str(quote)).strip().lower()
    if not q or q in ("none", "null", "nan"):
        return False
    if len(q) < min_chars:
        return False
    for src in sources:
        if not src:
            continue
        s = re.sub(r"\s+", " ", str(src)).strip().lower()
        if q in s:
            return True
    return False


def subtype_quote_passes(
    typology_code: Optional[str],
    typology_evidence_quote: Optional[str],
) -> Tuple[bool, str]:
    """F4 token-requirement check.

    Returns (ok, reason). If the code has no requirements registered (e.g.
    'None' or unknown), passes vacuously.
    """
    if not typology_code or typology_code in ("None", "none", ""):
        return True, "no typology code"
    req = SUBTYPE_REQUIREMENTS.get(typology_code)
    if req is None:
        return True, f"no requirement set for code {typology_code!r}"

    hay = _haystack(typology_evidence_quote)
    hits: list[str] = []
    for group in req:
        match = _any_token_in(hay, group)
        if match is None:
            sample = ", ".join(list(group)[:4])
            return False, (
                f"evidence quote missing one of: [{sample}, ...]"
            )
        hits.append(match)
    return True, f"matched: {hits}"


def _primary_prefix(typology_code: str) -> Optional[str]:
    if typology_code.startswith("NBS-"):
        return "NBS"
    if typology_code.startswith("RE-"):
        return "RE"
    if typology_code.startswith("M-"):
        return "M"
    if typology_code.startswith("A-"):
        return "A"
    return None


def subtype_context_passes(
    typology_code: Optional[str],
    typology_evidence_quote: Optional[str],
    *,
    policy_statement: Optional[str] = "",
    verbatim_text: Optional[str] = "",
) -> Tuple[bool, str]:
    """Less brittle F4 pass for non-rare codes.

    The evidence quote still has to be source-grounded and carry the secondary
    subtype token. The wider row context may carry the primary/climate token.
    This rescues cases like building-efficiency rows whose quote says "EPC" or
    "energy performance" while the row context supplies "carbon/emissions".
    """
    if not typology_code or typology_code in ("None", "none", ""):
        return True, "no typology code"
    if typology_code in STRICT_ONLY_SUBTYPE_CODES:
        return False, f"contextual pass disabled for rare code {typology_code!r}"

    subtype_tokens = SUBTYPE_SIGNAL_TOKENS.get(typology_code)
    prefix = _primary_prefix(typology_code)
    primary_tokens = PRIMARY_SIGNAL_TOKENS.get(prefix or "")
    if subtype_tokens is None or primary_tokens is None:
        return False, f"no contextual requirement set for code {typology_code!r}"

    quote_hay = _haystack(typology_evidence_quote)
    subtype_hit = _any_token_in(quote_hay, subtype_tokens)
    if subtype_hit is None:
        sample = ", ".join(list(subtype_tokens)[:4])
        return False, (
            f"evidence quote missing secondary subtype token: [{sample}, ...]"
        )

    context_hay = _haystack(policy_statement, verbatim_text)
    primary_hit = _any_token_in(context_hay, primary_tokens)
    if primary_hit is None:
        sample = ", ".join(list(primary_tokens)[:4])
        return False, (
            f"row context missing primary/climate token: [{sample}, ...]"
        )

    return True, (
        "contextual_pass: "
        f"quote matched secondary token {subtype_hit!r}; "
        f"row context matched primary token {primary_hit!r}"
    )


def secondary_passes_gate(
    typology_code: Optional[str],
    typology_evidence_quote: Optional[str],
    *,
    policy_statement: Optional[str] = "",
    verbatim_text: Optional[str] = "",
) -> Tuple[bool, str]:
    """Combined F4 gate: substring grounding + per-subtype token check.

    First try the strict check where the quote contains every required token
    group. If that fails, allow a contextual pass for non-rare subtype codes:
    the quote must contain the secondary subtype signal, while the wider row
    text can supply the primary/climate linkage.
    """
    if not typology_code or typology_code in ("None", "none", ""):
        return True, "no typology code"
    if not is_substring_grounded(
        typology_evidence_quote, policy_statement, verbatim_text
    ):
        return False, "evidence_quote not substring-grounded in source"
    strict_ok, strict_reason = subtype_quote_passes(
        typology_code, typology_evidence_quote
    )
    if strict_ok:
        return strict_ok, strict_reason

    context_ok, context_reason = subtype_context_passes(
        typology_code,
        typology_evidence_quote,
        policy_statement=policy_statement,
        verbatim_text=verbatim_text,
    )
    if context_ok:
        return context_ok, context_reason
    return False, f"{strict_reason}; {context_reason}"


# ---------------------------------------------------------------------------
# Dry-run helpers — apply gates over a list of pipeline records.
# ---------------------------------------------------------------------------

def apply_primary_gate_to_record(record: dict) -> dict:
    """Stamp `primary_gate_passed` and `primary_gate_reason` on a record.

    Does NOT demote primary_category. Mutates and returns the record.
    """
    ok, reason = primary_passes_gate(
        record.get("primary_category"),
        policy_statement=record.get("policy_statement", ""),
        verbatim_text=record.get("verbatim_text", ""),
        canonical_mechanism=record.get("canonical_mechanism", ""),
    )
    record["primary_gate_passed"] = ok
    record["primary_gate_reason"] = reason
    return record


def apply_secondary_gate_to_record(record: dict) -> dict:
    """Apply F4 to one record. Demotes if the gate fails. Mutates in place."""
    code = record.get("typology_code")
    if code is None or (isinstance(code, float) and code != code):
        # NaN / None — nothing to gate
        record.setdefault("secondary_gate_passed", True)
        record.setdefault("secondary_gate_reason", "no code present")
        return record

    ok, reason = secondary_passes_gate(
        code,
        record.get("typology_evidence_quote"),
        policy_statement=record.get("policy_statement", ""),
        verbatim_text=record.get("verbatim_text", ""),
    )
    record["secondary_gate_passed"] = ok
    record["secondary_gate_reason"] = reason
    if not ok:
        record["typology_code"] = None
        record["typology_evidence_quote"] = None
        record["typology_confidence"] = 0.0
        record["secondary_categories"] = None
        record["gate_demoted_reason"] = reason
    return record


# ---------------------------------------------------------------------------
# F1 — financial-instrument deterministic override
# ---------------------------------------------------------------------------

# Reuse the keyword set already defined in consistent_classification — we
# replicate it here to avoid the heavy import chain in case someone wants
# this module standalone.
_FIN_KEYWORDS = (
    "fee", "fees", "pricing", "carbon pricing", "tax", "taxes", "levy", "levies",
    "rebate", "rebates", "grant", "grants", "subsidy", "subsidies",
    "tax credit", "tax credits", "loan", "loans", "financing",
    "fund", "funding", "trust fund",
    "appropriation", "appropriations",
    "bond", "bonds",
    "cost share",
)


def is_financial_instrument_in_source(
    *sources: Optional[str],
) -> bool:
    """Deterministic check: do any of the financial-instrument keywords
    appear in the SOURCE text? Used to override LM false positives."""
    hay = _haystack(*sources)
    return _any_token_in(hay, _FIN_KEYWORDS) is not None


def apply_financial_instrument_gate_to_record(record: dict) -> dict:
    """One-direction override: if LM said 'yes' but no FI keyword is in the
    source, downgrade to 'no'. Never upgrades."""
    current = record.get("is_financial_instrument")
    if str(current).lower() != "yes":
        return record
    if not is_financial_instrument_in_source(
        record.get("policy_statement", ""),
        record.get("verbatim_text", ""),
    ):
        record["is_financial_instrument"] = "no"
        record["financial_instrument_overridden"] = True
        record["financial_instrument_override_reason"] = (
            "no FI keyword in policy_statement/verbatim_text"
        )
    return record


def apply_F1_F4_to_records(records: list[dict]) -> list[dict]:
    """Run all three gates (primary + secondary + financial instrument)
    over a list of pipeline records. Mutates in place. Idempotent.
    """
    for r in records:
        apply_primary_gate_to_record(r)
        apply_secondary_gate_to_record(r)
        apply_financial_instrument_gate_to_record(r)
    return records
