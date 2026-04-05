"""
ExtractionPipeline — Plain Python orchestrator (no DSPy dependency).

Wires DocumentChunker, PolicyExtractor, and PolicyResolver together.
The notebook calls ``pipeline.run()`` — nothing else changes.

Fixes applied:
- #1:  Hierarchical carry-forward summary (parents, subs, individuals)
- #4:  Chunk provenance as pipeline metadata dict
- #5:  All documents go through the resolver (no single-chunk fast path)
- #8:  ChunkResult dataclass + chunk_trace.json output per city
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .chunking import Chunk, DocumentChunker
from .dspy_extraction import PolicyExtractor
from .dspy_resolve import PolicyResolver, ResolverResult, ResolverStats, _similarity
from .schemas import DocumentMetadata, ExtractedPolicy

# Minimum similarity for matching subs to parents in the carry-forward summary
_SUMMARY_MATCH_THRESHOLD = 0.80


# =============================================================================
# ChunkResult — per-chunk extraction metrics (Fix #8)
# =============================================================================

@dataclass
class ChunkResult:
    """Metrics for a single chunk extraction."""

    chunk_index: int
    word_count: int
    has_overlap: bool
    ancestor_headings: List[str]
    policies_extracted: int
    elapsed_seconds: float
    error: Optional[str] = None
    carry_forward_length: int = 0        # chars of summary passed to extractor


# =============================================================================
# PipelineResult — structured return type (Fixes #4, #8, #9)
# =============================================================================

@dataclass
class PipelineResult:
    """Complete result from ExtractionPipeline.run()."""

    policies: List[ExtractedPolicy]

    # Fix #4: chunk provenance mapping
    chunk_provenance: Dict[str, int] = field(default_factory=dict)
    # Maps policy_statement -> chunk_index for every extracted policy

    # Fix #8: per-chunk extraction metrics
    chunk_results: List[ChunkResult] = field(default_factory=list)

    # Fix #9: resolver statistics
    resolver_stats: Optional[ResolverStats] = None

    # Summary
    total_chunks: int = 0
    doc_id: Optional[str] = None

    @property
    def failed_chunks(self) -> List[ChunkResult]:
        return [cr for cr in self.chunk_results if cr.error]

    @property
    def successful_chunks(self) -> List[ChunkResult]:
        return [cr for cr in self.chunk_results if not cr.error]


# =============================================================================
# ExtractionPipeline
# =============================================================================

class ExtractionPipeline:
    """Orchestrates chunking, extraction, and resolution.

    Parameters
    ----------
    extractor : PolicyExtractor
        DSPy module for single-chunk policy extraction.
    chunker : DocumentChunker
        Pure Python document chunker.
    resolver : PolicyResolver
        Three-stage resolver for dedup + linking.
    """

    def __init__(
        self,
        extractor: PolicyExtractor,
        chunker: DocumentChunker,
        resolver: PolicyResolver,
        max_chunk_retries: int = 1,
        initial_retry_delay_seconds: float = 1.0,
        max_retry_delay_seconds: float = 30.0,
    ):
        self.extractor = extractor
        self.chunker = chunker
        self.resolver = resolver
        self.max_chunk_retries = max_chunk_retries
        self.initial_retry_delay_seconds = initial_retry_delay_seconds
        self.max_retry_delay_seconds = max_retry_delay_seconds

    @staticmethod
    def _is_quota_error(error: Exception) -> bool:
        """Detect non-retryable quota exhaustion errors."""
        message = str(error).lower()
        return (
            "insufficient_quota" in message
            or "exceeded your current quota" in message
            or "check your plan and billing details" in message
        )

    def _retry_delay_seconds(self, attempt: int) -> float:
        """Exponential backoff with a small jitter."""
        base_delay = self.initial_retry_delay_seconds * (2 ** max(attempt - 1, 0))
        jitter = random.uniform(0, 0.5)
        return min(base_delay + jitter, self.max_retry_delay_seconds)

    # ------------------------------------------------------------------ #
    # Fix #1: Hierarchical carry-forward summary
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_summary(policies: List[ExtractedPolicy]) -> str:
        """Build a compact hierarchical index of all extracted policies.

        Format (Fix #1 — includes all policy types, not just parents):

            P1: <parent statement> — <section_header>
              S1.1: <sub statement>
              S1.2: <sub statement>
            P2: <parent statement> — <section_header>
              S2.1: <sub statement>
            I1: <individual statement> — <section_header>
            I2: <individual statement> — <section_header>

        ~25 tokens per entry.  Pure string template — no LLM call.
        """
        if not policies:
            return ""

        # Group subs under their parents
        parents = [p for p in policies if p.policy_type == "parent"]
        subs = [p for p in policies if p.policy_type == "sub"]
        individuals = [p for p in policies if p.policy_type == "individual"]

        # Build parent -> sub mapping
        parent_subs: Dict[str, List[ExtractedPolicy]] = {}
        for parent in parents:
            parent_subs[parent.policy_statement] = []

        unmatched_subs: List[ExtractedPolicy] = []
        for sub in subs:
            matched = False
            if sub.parent_policy_name:
                best_score = 0.0
                best_pstmt: Optional[str] = None
                for pstmt in parent_subs:
                    score = _similarity(sub.parent_policy_name, pstmt)
                    if score > best_score:
                        best_score = score
                        best_pstmt = pstmt
                if best_score >= _SUMMARY_MATCH_THRESHOLD and best_pstmt is not None:
                    parent_subs[best_pstmt].append(sub)
                    matched = True
            if not matched:
                unmatched_subs.append(sub)

        lines: List[str] = []

        # Parents with their subs
        for pi, parent in enumerate(parents, 1):
            lines.append(
                f"P{pi}: {parent.policy_statement[:100]} — {parent.section_header}"
            )
            for si, sub in enumerate(parent_subs.get(parent.policy_statement, []), 1):
                lines.append(f"  S{pi}.{si}: {sub.policy_statement[:90]}")

        # Unmatched subs (orphans from current chunk)
        for sub in unmatched_subs:
            parent_ref = sub.parent_policy_name or "unknown"
            lines.append(f"  S?: {sub.policy_statement[:90]} (parent: {parent_ref})")

        # Individuals
        for ii, indiv in enumerate(individuals, 1):
            lines.append(
                f"I{ii}: {indiv.policy_statement[:100]} — {indiv.section_header}"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #

    def run(
        self,
        document_text: str,
        document_metadata: DocumentMetadata,
    ) -> PipelineResult:
        """Run the full extraction pipeline on a single document.

        Fix #5: ALL documents go through the resolver, even single-chunk ones.
        This ensures consistent dedup and orphan logging.
        """
        doc_id = document_metadata.doc_id or "unknown"

        # -- Chunk the document --
        chunks = self.chunker.split(document_text)
        print(f"  [{doc_id}] {len(chunks)} chunk(s), "
              f"{len(document_text.split())} total words")

        # -- Extract from each chunk sequentially --
        accumulated: List[ExtractedPolicy] = []
        chunk_results: List[ChunkResult] = []
        chunk_provenance: Dict[str, int] = {}

        for chunk in chunks:
            summary = self._build_summary(accumulated)

            # Fix #6: Retry with backoff on chunk extraction failure
            new_policies = None
            last_error = None
            t0 = time.time()

            total_attempts = 1 + self.max_chunk_retries
            for attempt in range(total_attempts):
                try:
                    new_policies = self.extractor(
                        document_text=chunk.text,
                        document_metadata=document_metadata,
                        prior_policies_summary=summary,
                    )
                    break  # success
                except Exception as e:
                    last_error = e
                    if self._is_quota_error(e):
                        print(f"    [{doc_id}] chunk {chunk.index} hit non-retryable quota error: {e}")
                        break
                    if attempt < self.max_chunk_retries:
                        wait = self._retry_delay_seconds(attempt + 1)
                        print(f"    [{doc_id}] chunk {chunk.index} attempt "
                              f"{attempt + 1} failed: {e}, retrying in {wait:.1f}s")
                        time.sleep(wait)

            elapsed = time.time() - t0

            if new_policies is not None:
                # Fix #7: Post-extraction normalization
                new_policies = self._normalize_policies(new_policies)

                # Fix #4: Record chunk provenance
                for p in new_policies:
                    chunk_provenance[p.policy_statement] = chunk.index

                chunk_results.append(ChunkResult(
                    chunk_index=chunk.index,
                    word_count=chunk.word_count,
                    has_overlap=chunk.has_overlap,
                    ancestor_headings=chunk.ancestor_headings,
                    policies_extracted=len(new_policies),
                    elapsed_seconds=round(elapsed, 2),
                    carry_forward_length=len(summary),
                ))

                print(f"    chunk {chunk.index + 1}/{len(chunks)}: "
                      f"{len(new_policies)} policies ({elapsed:.1f}s)")
                accumulated.extend(new_policies)

            else:
                chunk_results.append(ChunkResult(
                    chunk_index=chunk.index,
                    word_count=chunk.word_count,
                    has_overlap=chunk.has_overlap,
                    ancestor_headings=chunk.ancestor_headings,
                    policies_extracted=0,
                    elapsed_seconds=round(elapsed, 2),
                    error=str(last_error),
                    carry_forward_length=len(summary),
                ))
                print(f"    [{doc_id}] chunk {chunk.index} failed after "
                      f"{total_attempts} attempts: {last_error}")
                continue  # partial result preserved

        # -- Fix #5: Always resolve (even single-chunk documents) --
        resolver_result: ResolverResult = self.resolver.resolve(accumulated)
        stats = resolver_result.stats

        if stats.dedup_removed or stats.statement_dedup_removed:
            print(f"    [{doc_id}] dedup: {stats.dedup_removed} verbatim, "
                  f"{stats.statement_dedup_removed} statement")
        if stats.deterministic_links:
            print(f"    [{doc_id}] linked: {stats.deterministic_links} deterministic")
        if stats.llm_arbitrated:
            print(f"    [{doc_id}] arbitrated: {stats.llm_arbitrated} via LLM")
        if stats.orphan_count:
            print(f"    [{doc_id}] {stats.orphan_count} orphaned sub-policies")

        return PipelineResult(
            policies=resolver_result.policies,
            chunk_provenance=chunk_provenance,
            chunk_results=chunk_results,
            resolver_stats=stats,
            total_chunks=len(chunks),
            doc_id=doc_id,
        )

    # ------------------------------------------------------------------ #
    # Fix #7: Post-extraction normalization
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_policies(
        policies: List[ExtractedPolicy],
    ) -> List[ExtractedPolicy]:
        """Normalize LLM output fields to prevent downstream mismatches.

        - Lowercases policy_type to match Literal["parent", "sub", "individual"]
        - Strips whitespace from all string fields
        - Drops policies with empty policy_statement or verbatim_text
        """
        valid_types = {"parent", "sub", "individual"}
        cleaned: List[ExtractedPolicy] = []

        for p in policies:
            # Normalize policy_type
            pt = p.policy_type.strip().lower() if p.policy_type else ""
            if pt not in valid_types:
                print(f"    [normalize] dropped policy with invalid type '{p.policy_type}': "
                      f"{p.policy_statement[:60]}")
                continue

            # Reject empty required fields
            stmt = p.policy_statement.strip() if p.policy_statement else ""
            verb = p.verbatim_text.strip() if p.verbatim_text else ""
            if not stmt or not verb:
                print(f"    [normalize] dropped policy with empty statement/verbatim")
                continue

            # Apply normalized values
            p.policy_type = pt
            p.policy_statement = stmt
            p.verbatim_text = verb
            p.section_header = p.section_header.strip() if p.section_header else ""
            p.sector = p.sector.strip() if p.sector else ""
            if p.parent_policy_name:
                p.parent_policy_name = p.parent_policy_name.strip()

            cleaned.append(p)

        dropped = len(policies) - len(cleaned)
        if dropped:
            print(f"    [normalize] dropped {dropped} invalid policies")

        return cleaned

    # ------------------------------------------------------------------ #
    # Fix #8: Chunk trace export
    # ------------------------------------------------------------------ #

    @staticmethod
    def export_chunk_trace(
        result: PipelineResult,
        output_dir: str | Path,
    ) -> Path:
        """Write chunk_trace.json to the output directory.

        Contains per-chunk extraction metrics and resolver stats for
        full pipeline observability.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        trace = {
            "doc_id": result.doc_id,
            "total_chunks": result.total_chunks,
            "total_policies_before_resolve": sum(
                cr.policies_extracted for cr in result.chunk_results
            ),
            "total_policies_after_resolve": len(result.policies),
            "resolver_stats": {
                "input_count": result.resolver_stats.input_count,
                "dedup_removed": result.resolver_stats.dedup_removed,
                "statement_dedup_removed": result.resolver_stats.statement_dedup_removed,
                "deterministic_links": result.resolver_stats.deterministic_links,
                "llm_arbitrated": result.resolver_stats.llm_arbitrated,
                "orphan_count": result.resolver_stats.orphan_count,
                "output_count": result.resolver_stats.output_count,
            } if result.resolver_stats else None,
            "chunks": [
                {
                    "chunk_index": cr.chunk_index,
                    "word_count": cr.word_count,
                    "has_overlap": cr.has_overlap,
                    "ancestor_headings": cr.ancestor_headings,
                    "policies_extracted": cr.policies_extracted,
                    "elapsed_seconds": cr.elapsed_seconds,
                    "carry_forward_length": cr.carry_forward_length,
                    "error": cr.error,
                }
                for cr in result.chunk_results
            ],
            "chunk_provenance": result.chunk_provenance,
        }

        trace_path = output_dir / "chunk_trace.json"
        with open(trace_path, "w", encoding="utf-8") as f:
            json.dump(trace, f, ensure_ascii=False, indent=2)

        return trace_path
