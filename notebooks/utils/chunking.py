"""
DocumentChunker — Pure Python, no DSPy dependency.

Splits long documents into LLM-friendly chunks using heading-aware
greedy bin-packing with an overlap window for boundary continuity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


# =============================================================================
# Configurable heading scorer weights (Fix #6)
# =============================================================================

@dataclass
class HeadingWeights:
    """Weights for the unified heading scorer.

    Each feature is scored independently; the total determines whether a line
    is treated as a heading.  Tune per document type (e.g. NDCs vs. municipal
    action plans have different heading conventions).
    """

    markdown_heading: float = 10.0       # starts with '#'
    short_uppercase: float = 8.0         # <= max_heading_words AND fully uppercase
    bold_standalone: float = 6.0         # wrapped in **...** with no trailing body
    numbered_pattern: float = 5.0        # matches 1., 1.1, Section 3, PART II, etc.
    blank_line_surround: float = 2.0     # has blank line before AND after
    max_heading_words: int = 10          # cap for "short" heuristic
    threshold: float = 5.0              # minimum score to be labelled a heading


@dataclass
class Chunk:
    """One segment of a chunked document."""

    index: int
    text: str                                  # body text (may include overlap prefix)
    ancestor_headings: List[str] = field(default_factory=list)
    word_count: int = 0
    has_overlap: bool = False


# =============================================================================
# Budget constants
# =============================================================================

MIN_CHUNK_WORDS = 800                   # floor to prevent degenerate tiny chunks


# =============================================================================
# DocumentChunker
# =============================================================================

class DocumentChunker:
    """Heading-aware greedy bin-packing chunker with overlap window.

    Parameters
    ----------
    words_per_chunk : int
        Ceiling word count per chunk.  Effective budget shrinks as the
        carry-forward summary grows (see ``chunk_budget``).
    model_context_limit : int
        Model context window in tokens (used for adaptive budget).
    overlap_paragraphs : int
        Number of trailing paragraphs from chunk N to prepend to chunk N+1
        as ``[OVERLAP CONTEXT]``.
    heading_weights : HeadingWeights | None
        Custom heading scorer weights.  ``None`` uses defaults.
    """

    def __init__(
        self,
        words_per_chunk: int = 6000,
        overlap_paragraphs: int = 2,
        heading_weights: Optional[HeadingWeights] = None,
    ):
        self.words_per_chunk = words_per_chunk
        self.overlap_paragraphs = overlap_paragraphs
        self.weights = heading_weights or HeadingWeights()

        # Compiled regex patterns
        self._re_numbered = re.compile(
            r"^(?:Section\s+\d|PART\s+[IVXLC]+|\d+(?:\.\d+)*\.?\s)",
            re.IGNORECASE,
        )
        self._re_bold_standalone = re.compile(r"^\*\*[^*]+\*\*\s*$")

    # --------------------------------------------------------------------- #
    # Heading scoring
    # --------------------------------------------------------------------- #

    def _score_line(self, line: str, prev_blank: bool, next_blank: bool) -> float:
        """Score a single line for heading-ness using weighted features."""
        score = 0.0
        stripped = line.strip()
        if not stripped:
            return 0.0

        words = stripped.split()

        if stripped.startswith("#"):
            score += self.weights.markdown_heading

        if (
            len(words) <= self.weights.max_heading_words
            and stripped == stripped.upper()
            and any(c.isalpha() for c in stripped)
        ):
            score += self.weights.short_uppercase

        if self._re_bold_standalone.match(stripped):
            score += self.weights.bold_standalone

        if self._re_numbered.match(stripped):
            score += self.weights.numbered_pattern

        if prev_blank and next_blank:
            score += self.weights.blank_line_surround

        return score

    def _find_headings(self, lines: List[str]) -> List[int]:
        """Return indices of lines scored above the heading threshold."""
        heading_indices: List[int] = []
        for i, line in enumerate(lines):
            prev_blank = (i == 0) or (lines[i - 1].strip() == "")
            next_blank = (i == len(lines) - 1) or (lines[i + 1].strip() == "")
            if self._score_line(line, prev_blank, next_blank) >= self.weights.threshold:
                heading_indices.append(i)
        return heading_indices

    # --------------------------------------------------------------------- #
    # Section splitting
    # --------------------------------------------------------------------- #

    @dataclass
    class _Section:
        heading: str
        text: str
        word_count: int

    def _split_into_sections(self, text: str) -> List[_Section]:
        """Split text into sections delimited by detected headings."""
        lines = text.split("\n")
        heading_indices = self._find_headings(lines)

        if not heading_indices:
            # No headings found — treat entire text as one section
            wc = len(text.split())
            return [self._Section(heading="", text=text, word_count=wc)]

        sections: List[DocumentChunker._Section] = []

        # Text before the first heading (if any)
        if heading_indices[0] > 0:
            pre_text = "\n".join(lines[: heading_indices[0]])
            wc = len(pre_text.split())
            if wc > 0:
                sections.append(self._Section(heading="", text=pre_text, word_count=wc))

        for idx, h_idx in enumerate(heading_indices):
            end = heading_indices[idx + 1] if idx + 1 < len(heading_indices) else len(lines)
            heading = lines[h_idx].strip().lstrip("#").strip()
            body = "\n".join(lines[h_idx:end])
            wc = len(body.split())
            sections.append(self._Section(heading=heading, text=body, word_count=wc))

        return sections

    # --------------------------------------------------------------------- #
    # Adaptive budget
    # --------------------------------------------------------------------- #

    @property
    def budget(self) -> int:
        """Effective word budget per chunk.

        Note: Chunking happens once before extraction, so the budget is fixed.
        The carry-forward summary is handled by the LLM's context window —
        words_per_chunk should be set conservatively to leave room for it.
        """
        return max(self.words_per_chunk, MIN_CHUNK_WORDS)

    # --------------------------------------------------------------------- #
    # Paragraph fallback for oversized sections
    # --------------------------------------------------------------------- #

    @staticmethod
    def _split_by_paragraphs(text: str, budget: int) -> List[str]:
        """Split text into chunks by paragraph boundaries, respecting budget."""
        paragraphs = re.split(r"\n\s*\n", text)
        chunks: List[str] = []
        current: List[str] = []
        current_wc = 0

        for para in paragraphs:
            para_wc = len(para.split())
            if current_wc + para_wc > budget and current:
                chunks.append("\n\n".join(current))
                current = [para]
                current_wc = para_wc
            else:
                current.append(para)
                current_wc += para_wc

        if current:
            chunks.append("\n\n".join(current))

        return chunks

    # --------------------------------------------------------------------- #
    # Overlap prefix
    # --------------------------------------------------------------------- #

    @staticmethod
    def _extract_overlap(text: str, n_paragraphs: int) -> str:
        """Extract the last N paragraphs from text for overlap."""
        if n_paragraphs <= 0:
            return ""
        paragraphs = re.split(r"\n\s*\n", text.rstrip())
        tail = paragraphs[-n_paragraphs:]
        return "[OVERLAP CONTEXT]\n" + "\n\n".join(tail)

    # --------------------------------------------------------------------- #
    # Main split entry point
    # --------------------------------------------------------------------- #

    def split(self, text: str) -> List[Chunk]:
        """Split a document into chunks.

        Parameters
        ----------
        text : str
            Full document text.

        Returns
        -------
        list[Chunk]
            Ordered chunks with metadata.
        """
        budget = self.budget
        total_words = len(text.split())

        # Short document — single chunk, no-op
        if total_words <= budget:
            return [Chunk(index=0, text=text, word_count=total_words)]

        sections = self._split_into_sections(text)

        # Greedy bin-packing: accumulate sections into bins
        bins: List[List[DocumentChunker._Section]] = []
        current_bin: List[DocumentChunker._Section] = []
        current_wc = 0

        for section in sections:
            if section.word_count > budget:
                # Flush current bin first
                if current_bin:
                    bins.append(current_bin)
                    current_bin = []
                    current_wc = 0
                # This section itself is oversized — paragraph fallback
                combined_text = section.text
                para_chunks = self._split_by_paragraphs(combined_text, budget)
                for pc in para_chunks:
                    pseudo = self._Section(
                        heading=section.heading, text=pc, word_count=len(pc.split())
                    )
                    bins.append([pseudo])
            elif current_wc + section.word_count > budget and current_bin:
                bins.append(current_bin)
                current_bin = [section]
                current_wc = section.word_count
            else:
                current_bin.append(section)
                current_wc += section.word_count

        if current_bin:
            bins.append(current_bin)

        # Build chunks with overlap
        chunks: List[Chunk] = []
        prev_overlap = ""

        for i, bin_sections in enumerate(bins):
            # Combine sections in this bin
            body = "\n\n".join(s.text for s in bin_sections)

            # Prepend overlap from previous chunk
            if prev_overlap:
                full_text = prev_overlap + "\n\n" + body
                has_overlap = True
            else:
                full_text = body
                has_overlap = False

            ancestor_headings = [s.heading for s in bin_sections if s.heading]

            chunks.append(
                Chunk(
                    index=i,
                    text=full_text,
                    ancestor_headings=ancestor_headings,
                    word_count=len(full_text.split()),
                    has_overlap=has_overlap,
                )
            )

            # Prepare overlap for next chunk
            prev_overlap = self._extract_overlap(body, self.overlap_paragraphs)

        return chunks
