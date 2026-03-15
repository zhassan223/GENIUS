from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ExtractedPolicy(BaseModel):
    """
    Canonical policy object produced by the extractor and carried through the pipeline.
    """

    policy_statement: str = Field(
        description=(
            "A concise, self-contained summary of the policy commitment. "
            "MUST be directly supported by verbatim_text."
        )
    )
    verbatim_text: str = Field(
        description=(
            "Exact text from source document that supports this policy (prefer 2–3 sentences). "
            "Used for grounding verification."
        )
    )

    policy_type: Literal["parent", "sub", "individual"] = Field(
        description=(
            "'parent': Umbrella program/plan with sub-policies listed below it. "
            "'sub': Policy explicitly listed under a parent program. "
            "'individual': Standalone policy with no parent-child relationship."
        )
    )
    parent_policy_name: Optional[str] = Field(
        default=None,
        description="For 'sub' type only: the parent policy/program name as written in the document.",
    )

    section_header: str = Field(
        description="Section/subsection heading this policy appears under (copied from the document)."
    )
    sector: str = Field(description="Primary climate sector.")
    extraction_rationale: str = Field(
        description="Why this qualifies as a policy; note vagueness or edge cases."
    )


class DocumentMetadata(BaseModel):
    country: str
    state_or_province: Optional[str] = None
    city: Optional[str] = None
    doc_id: Optional[str] = None  # e.g. "Chicago_United_States_a3f9c1"


def make_doc_id(metadata: DocumentMetadata, text: str) -> str:
    """Generate a stable document ID from metadata + content hash.

    Normalizes whitespace before hashing so that reformatting the markdown
    source (trailing spaces, double newlines, etc.) does not change the ID.
    """
    import hashlib
    import re

    slug_parts = [metadata.city, metadata.state_or_province, metadata.country]
    slug = "_".join(p for p in slug_parts if p).replace(" ", "_")
    normalized = re.sub(r"\s+", " ", text).strip()
    h = hashlib.md5(normalized.encode()).hexdigest()[:10]
    return f"{slug}_{h}"

