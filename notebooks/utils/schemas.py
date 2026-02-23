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

