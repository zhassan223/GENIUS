from __future__ import annotations

import re
from typing import List, Tuple

from .schemas import ExtractedPolicy


def validate_grounding(
    policies: List[ExtractedPolicy],
    source_markdown: str,
    *,
    min_sentence_chars: int = 10,
    min_sentence_match_rate: float = 0.75,
) -> Tuple[List[ExtractedPolicy], List[dict]]:
    """
    Verify that verbatim_text components exist in the source markdown.

    Strategy:
    - Exact normalized verbatim match, else
    - sentence-level fragment match, requiring `min_sentence_match_rate`.
    """

    valid_policies: List[ExtractedPolicy] = []
    rejected_log: List[dict] = []

    source_normalized = " ".join(source_markdown.split())

    for policy in policies:
        verbatim = policy.verbatim_text or ""

        verbatim_normalized = " ".join(verbatim.split())
        if verbatim_normalized and verbatim_normalized in source_normalized:
            valid_policies.append(policy)
            continue

        # Split by sentence delimiters and ellipses.
        sentences = re.split(r"[.!?]\s+|\.\.\.\s+", verbatim)
        sentences = [s.strip() for s in sentences if len(s.strip()) > min_sentence_chars]

        if not sentences:
            rejected_log.append(
                {
                    "policy_statement": policy.policy_statement,
                    "reason": "Verbatim text too short or empty",
                    "verbatim_text": verbatim,
                }
            )
            continue

        found = 0
        missing = []
        for s in sentences:
            s_norm = " ".join(s.split())
            if s_norm in source_normalized:
                found += 1
            else:
                missing.append(s[:50] + "...")

        match_rate = found / len(sentences)
        if match_rate >= min_sentence_match_rate:
            valid_policies.append(policy)
        else:
            rejected_log.append(
                {
                    "policy_statement": policy.policy_statement,
                    "reason": (
                        f"Only {match_rate:.0%} of verbatim sentences found in source "
                        f"({found}/{len(sentences)})"
                    ),
                    "verbatim_text": (verbatim[:100] + "...") if len(verbatim) > 100 else verbatim,
                    "missing_sentences": missing,
                }
            )

    return valid_policies, rejected_log


def validate_grounding_fragments(
    policies: List[ExtractedPolicy],
    source_markdown: str,
    *,
    min_fragment_length: int = 20,
    min_fragment_match_rate: float = 0.5,
) -> Tuple[List[ExtractedPolicy], List[dict]]:
    """
    More lenient grounding check using key fragments split on ellipses/commas/semicolons.
    """

    valid_policies: List[ExtractedPolicy] = []
    rejected_log: List[dict] = []

    source_normalized = " ".join(source_markdown.split())

    for policy in policies:
        verbatim = policy.verbatim_text or ""

        fragments = re.split(r"\.\.\.|[,;]\s+", verbatim)
        fragments = [
            " ".join(f.split())
            for f in fragments
            if len(f.strip()) >= min_fragment_length
        ]

        if not fragments:
            rejected_log.append(
                {
                    "policy_statement": policy.policy_statement,
                    "reason": "No fragments of sufficient length to verify",
                    "verbatim_text": verbatim,
                }
            )
            continue

        found = sum(1 for frag in fragments if frag in source_normalized)
        match_rate = found / len(fragments)

        if match_rate >= min_fragment_match_rate:
            valid_policies.append(policy)
        else:
            rejected_log.append(
                {
                    "policy_statement": policy.policy_statement,
                    "reason": f"Only {match_rate:.0%} of verbatim fragments found ({found}/{len(fragments)})",
                    "verbatim_text": (verbatim[:100] + "...") if len(verbatim) > 100 else verbatim,
                    "fragments_checked": fragments[:3],
                }
            )

    return valid_policies, rejected_log

