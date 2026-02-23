from __future__ import annotations

from typing import Literal

import dspy
from pydantic import BaseModel, Field


YesNo = Literal["Yes", "No"]


class ValidationMetrics(BaseModel):
    """Refined soundness evaluation with stricter 'Individual' policy requirements."""

    # -- Core Criteria (Evidence-based only) --
    has_quantifiable_target: YesNo = Field(
        description=(
            "'Yes' ONLY if verbatim_text contains a measurable OUTCOME "
            "(e.g., % reduction, MW installed, units retrofitted). "
            "Budget figures alone ($X million) MUST be marked 'No' for this field."
        )
    )
    has_timeline: YesNo = Field(
        description=(
            "'Yes' ONLY if verbatim_text contains an explicit deadline or year (e.g., 'by 2030'). "
            "Vague terms like 'ongoing' or 'future' are 'No'."
        )
    )
    has_binding_mechanism: YesNo = Field(
        description="'Yes' if text indicates enforceable authority like an ordinance, mandate, or code change."
    )
    has_spatial_specificity: YesNo = Field(
        description="'Yes' if the policy specifies WHERE it applies (e.g., 'citywide', 'LMI neighborhoods')."
    )

    # -- Signal Flags --
    weak_language_detected: YesNo = Field(
        description=(
            "'Yes' if verbs like 'promote', 'encourage', 'explore', or 'aim' are present "
            "without a hard target."
        )
    )
    strong_language_detected: YesNo = Field(
        description="'Yes' if verbs like 'mandate', 'require', 'install', or 'achieve' are present with hard numbers."
    )

    # -- Verdicts --
    validation_result: Literal["VALID", "NON-SOUND"] = Field(
        description="Set to 'VALID' only if the STRICT VALID RULE passes. Otherwise 'NON-SOUND'."
    )
    confidence_score: float = Field(
        description=(
            "0.0-1.0. Cap at 0.4 if has_timeline='No' OR has_quantifiable_target='No' "
            "for individual policies."
        )
    )
    validation_reasoning: str = Field(
        description="Step-by-step justification quoting the exact evidence for each criteria."
    )
    final_verdict: bool = Field(
        description="True ONLY if validation_result == 'VALID' AND confidence_score >= 0.8."
    )


class PolicyValidationSignature(dspy.Signature):
    """
    Evaluate whether a climate policy statement is VALID (actionable + measurable)
    or NON-SOUND (vague/performative).

    STRICT VALID RULE (FOR INDIVIDUAL POLICIES):
      1) AUDITABLE COMMITMENT: clear deliverable, not just process/study
      2) GOLD STANDARD: has_timeline == 'Yes' AND has_quantifiable_target == 'Yes'
      3) LANGUAGE STRENGTH: weak_language_detected == 'No' OR has_binding_mechanism == 'Yes'

    Budget-only line items without measurable outcomes + deadlines should be NON-SOUND.
    """

    policy_statement: str = dspy.InputField(desc="Concise policy summary to evaluate")
    verbatim_text: str = dspy.InputField(desc="Original document text for grounding")
    sector: str = dspy.InputField(desc="Primary climate sector")
    extraction_rationale: str = dspy.InputField(desc="Initial extraction reasoning")

    validation_results: ValidationMetrics = dspy.OutputField(desc="Structured evaluation")


class PolicyValidator(dspy.Module):
    def __init__(self):
        super().__init__()
        self.validate = dspy.ChainOfThought(PolicyValidationSignature)

    def forward(self, policy_data: dict):
        return self.validate(
            policy_statement=policy_data.get("policy_statement", ""),
            verbatim_text=policy_data.get("verbatim_text", ""),
            sector=policy_data.get("sector", "General"),
            extraction_rationale=policy_data.get("extraction_rationale", ""),
        )

