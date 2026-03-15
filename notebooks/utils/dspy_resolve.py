"""
Three-stage policy resolver for cross-chunk extraction cleanup.

Stage 1 — Deduplicate: Remove near-duplicate policies (verbatim + statement)
Stage 2 — Deterministic link: Match sub.parent_policy_name to parent fields
Stage 3 — LLM arbitration: Batch resolve remaining unmatched subs

Fixes applied:
- #2b: Secondary dedup pass on policy_statement (lower threshold)
- #3:  Match against policy_statement, section_header, AND parent_policy_name
- #9:  Structured ResolverStats return type
"""

from __future__ import annotations

import difflib
import json
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import dspy

from .schemas import ExtractedPolicy


# =============================================================================
# Resolver statistics (Fix #9)
# =============================================================================

@dataclass
class ResolverStats:
    """Structured statistics from the resolution process."""

    input_count: int = 0
    dedup_removed: int = 0                # Stage 1: verbatim dedup
    statement_dedup_removed: int = 0      # Stage 1b: policy_statement dedup (Fix #2b)
    deterministic_links: int = 0          # Stage 2: matched without LLM
    llm_arbitrated: int = 0              # Stage 3: required LLM call
    orphan_count: int = 0                # unmatched subs after all stages
    output_count: int = 0


@dataclass
class ResolverResult:
    """Complete resolver output."""

    policies: List[ExtractedPolicy]
    stats: ResolverStats


# =============================================================================
# DSPy signature for Stage 3 (batched LLM arbitration)
# =============================================================================

class ParentSubArbitrationSignature(dspy.Signature):
    """Match unresolved sub-policies to their most likely parent policies.

    You are given:
    - A list of UNMATCHED sub-policies (each with an index and parent_policy_name)
    - A list of CANDIDATE parent policies (each with an index, policy_statement,
      and section_header)

    For each sub-policy, determine which parent it most likely belongs to based
    on semantic similarity between the sub's parent_policy_name and the parent's
    policy_statement/section_header.

    Return a JSON mapping: {"sub_index": "parent_index_or_null"}
    Use null if no parent is a reasonable match.
    """

    unmatched_subs: str = dspy.InputField(
        desc=(
            "JSON list of unmatched sub-policies. Each entry: "
            '{"index": int, "parent_policy_name": str, "policy_statement": str}'
        )
    )
    candidate_parents: str = dspy.InputField(
        desc=(
            "JSON list of candidate parent policies. Each entry: "
            '{"index": int, "policy_statement": str, "section_header": str}'
        )
    )

    mapping: str = dspy.OutputField(
        desc=(
            'JSON object mapping sub index to parent index or null. '
            'Example: {"0": 3, "1": null, "2": 0}'
        )
    )


# =============================================================================
# Normalization helpers
# =============================================================================

def _normalize(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _similarity(a: str, b: str) -> float:
    """Normalized string similarity using SequenceMatcher."""
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


# =============================================================================
# PolicyResolver
# =============================================================================

class PolicyResolver:
    """Three-stage resolver for cross-chunk extraction cleanup.

    Parameters
    ----------
    verbatim_dedup_threshold : float
        Similarity threshold for verbatim text deduplication (Stage 1).
    statement_dedup_threshold : float
        Similarity threshold for policy_statement deduplication (Stage 1b).
        Lower than verbatim to catch overlap-induced near-duplicates (Fix #2b).
    link_threshold : float
        Fuzzy match threshold for deterministic parent-sub linking (Stage 2).
    """

    def __init__(
        self,
        verbatim_dedup_threshold: float = 0.90,
        statement_dedup_threshold: float = 0.85,
        link_threshold: float = 0.85,
    ):
        self.verbatim_dedup_threshold = verbatim_dedup_threshold
        self.statement_dedup_threshold = statement_dedup_threshold
        self.link_threshold = link_threshold
        self._arbitrate = dspy.ChainOfThought(ParentSubArbitrationSignature)

    # ------------------------------------------------------------------ #
    # Stage 1: Deduplicate
    # ------------------------------------------------------------------ #

    def _deduplicate(
        self, policies: List[ExtractedPolicy]
    ) -> Tuple[List[ExtractedPolicy], int, int]:
        """Remove near-duplicates by verbatim text, then by policy_statement.

        When duplicates are found, keep the one with the longer verbatim_text.

        Returns (deduped_policies, verbatim_removed, statement_removed).
        """
        # 1a: Verbatim dedup
        keep: List[ExtractedPolicy] = []
        verbatim_removed = 0

        for policy in policies:
            is_dup = False
            for i, existing in enumerate(keep):
                sim = _similarity(policy.verbatim_text, existing.verbatim_text)
                if sim >= self.verbatim_dedup_threshold:
                    # Keep the one with longer verbatim
                    if len(policy.verbatim_text) > len(existing.verbatim_text):
                        keep[i] = policy
                    is_dup = True
                    verbatim_removed += 1
                    break
            if not is_dup:
                keep.append(policy)

        # 1b: Policy statement dedup (Fix #2b — catches overlap-induced dupes)
        final: List[ExtractedPolicy] = []
        statement_removed = 0

        for policy in keep:
            is_dup = False
            for i, existing in enumerate(final):
                sim = _similarity(policy.policy_statement, existing.policy_statement)
                if sim >= self.statement_dedup_threshold:
                    # Keep the one with longer verbatim
                    if len(policy.verbatim_text) > len(existing.verbatim_text):
                        final[i] = policy
                    is_dup = True
                    statement_removed += 1
                    break
            if not is_dup:
                final.append(policy)

        return final, verbatim_removed, statement_removed

    # ------------------------------------------------------------------ #
    # Stage 2: Deterministic link (Fix #3)
    # ------------------------------------------------------------------ #

    def _deterministic_link(
        self, policies: List[ExtractedPolicy]
    ) -> Tuple[List[ExtractedPolicy], List[int], int]:
        """Match sub.parent_policy_name to parent fields using fuzzy matching.

        Matches against ALL THREE fields (Fix #3):
        - parent.policy_statement
        - parent.section_header
        - parent.parent_policy_name (if present)

        Takes the best score across all three fields.

        Returns (policies, unmatched_sub_indices, link_count).
        """
        parents = [
            (i, p)
            for i, p in enumerate(policies)
            if p.policy_type == "parent"
        ]
        unmatched: List[int] = []
        link_count = 0

        for i, policy in enumerate(policies):
            if policy.policy_type != "sub" or not policy.parent_policy_name:
                continue

            # Check if parent_policy_name already matches a known parent
            best_score = 0.0
            best_parent_idx: Optional[int] = None
            query = policy.parent_policy_name

            for p_idx, parent in parents:
                # Match against all three fields, take the best
                scores = [
                    _similarity(query, parent.policy_statement),
                    _similarity(query, parent.section_header),
                ]
                if parent.parent_policy_name:
                    scores.append(_similarity(query, parent.parent_policy_name))

                max_score = max(scores)
                if max_score > best_score:
                    best_score = max_score
                    best_parent_idx = p_idx

            if best_score >= self.link_threshold and best_parent_idx is not None:
                # Update parent_policy_name to the canonical parent statement
                policies[i].parent_policy_name = policies[best_parent_idx].policy_statement
                link_count += 1
            else:
                unmatched.append(i)

        return policies, unmatched, link_count

    # ------------------------------------------------------------------ #
    # Stage 3: LLM arbitration
    # ------------------------------------------------------------------ #

    def _llm_arbitrate(
        self, policies: List[ExtractedPolicy], unmatched_indices: List[int]
    ) -> Tuple[List[ExtractedPolicy], int]:
        """Use a single batched LLM call to resolve remaining unmatched subs.

        Returns (policies, arbitrated_count).
        """

        parents = [
            (i, p) for i, p in enumerate(policies) if p.policy_type == "parent"
        ]

        if not parents:
            return policies, 0

        # Build inputs for the LLM
        sub_entries = []
        for idx in unmatched_indices:
            p = policies[idx]
            sub_entries.append({
                "index": idx,
                "parent_policy_name": p.parent_policy_name or "",
                "policy_statement": p.policy_statement,
            })

        parent_entries = []
        for p_idx, parent in parents:
            parent_entries.append({
                "index": p_idx,
                "policy_statement": parent.policy_statement,
                "section_header": parent.section_header,
            })

        # Retry LLM arbitration up to 2 attempts
        last_error = None
        for attempt in range(2):
            try:
                result = self._arbitrate(
                    unmatched_subs=json.dumps(sub_entries),
                    candidate_parents=json.dumps(parent_entries),
                )

                raw = result.mapping
                # Handle "null" string from LLM
                if isinstance(raw, str):
                    raw = raw.strip()
                    if raw.lower() in ("null", "none", ""):
                        return policies, 0
                    mapping = json.loads(raw)
                else:
                    mapping = raw

                if not isinstance(mapping, dict):
                    print(f"  [resolver] LLM returned non-dict mapping: {type(mapping)}")
                    return policies, 0

                arbitrated = 0

                # Per-entry defensive parsing
                for sub_idx_str, parent_idx in mapping.items():
                    try:
                        sub_idx = int(sub_idx_str)
                        if parent_idx is None or sub_idx not in unmatched_indices:
                            continue
                        parent_idx = int(parent_idx)
                        if 0 <= parent_idx < len(policies) and policies[parent_idx].policy_type == "parent":
                            policies[sub_idx].parent_policy_name = policies[parent_idx].policy_statement
                            arbitrated += 1
                    except (ValueError, TypeError, IndexError) as entry_err:
                        print(f"  [resolver] skipping malformed entry "
                              f"sub={sub_idx_str} parent={parent_idx}: {entry_err}")
                        continue

                return policies, arbitrated

            except Exception as e:
                last_error = e
                if attempt < 1:
                    print(f"  [resolver] LLM arbitration attempt {attempt + 1} failed: {e}, retrying...")
                    time.sleep(1)

        print(f"  [resolver] LLM arbitration failed after 2 attempts: {last_error}")
        return policies, 0

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #

    def resolve(self, policies: List[ExtractedPolicy]) -> ResolverResult:
        """Run the full three-stage resolution pipeline.

        Returns a ResolverResult with cleaned policies and detailed stats.
        """
        stats = ResolverStats(input_count=len(policies))

        if not policies:
            stats.output_count = 0
            return ResolverResult(policies=[], stats=stats)

        # Stage 1: Deduplicate
        policies, verbatim_removed, statement_removed = self._deduplicate(policies)
        stats.dedup_removed = verbatim_removed
        stats.statement_dedup_removed = statement_removed

        # Stage 2: Deterministic link
        policies, unmatched, link_count = self._deterministic_link(policies)
        stats.deterministic_links = link_count

        # Stage 3: LLM arbitration (only if needed)
        if unmatched:
            policies, arbitrated = self._llm_arbitrate(policies, unmatched)
            stats.llm_arbitrated = arbitrated

        # Count remaining orphans: subs with no parent_policy_name OR
        # parent_policy_name that doesn't match any actual parent
        parent_stmts = {
            p.policy_statement for p in policies if p.policy_type == "parent"
        }
        stats.orphan_count = sum(
            1 for p in policies
            if p.policy_type == "sub"
            and (not p.parent_policy_name or p.parent_policy_name not in parent_stmts)
        )
        stats.output_count = len(policies)

        return ResolverResult(policies=policies, stats=stats)
