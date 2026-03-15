from __future__ import annotations

from typing import List

import dspy

from .schemas import DocumentMetadata, ExtractedPolicy


class PolicyExtractionSignature(dspy.Signature):
    """
    Extract climate policies from document text.

    CRITICAL: Extract ONLY information explicitly present in the document.
    Every policy MUST have verbatim text that can be verified.

    ═══════════════════════════════════════════════════════════════════════
    OVERLAP CONTEXT RULE
    ═══════════════════════════════════════════════════════════════════════

    Text marked with "[OVERLAP CONTEXT]" at the beginning of the document
    is REPEATED from a prior chunk for continuity purposes only.

    DO NOT extract any policies from overlap sections. Overlap text exists
    solely to help you understand context for policies in the NEW text that
    follows. If a policy spans the boundary between overlap and new text,
    extract it using only the NEW text portion as verbatim_text.

    ═══════════════════════════════════════════════════════════════════════
    PRIOR POLICIES SUMMARY
    ═══════════════════════════════════════════════════════════════════════

    When prior_policies_summary is provided, it contains a compact index of
    policies already extracted from earlier chunks of the same document.

    USE THIS TO:
    • Link sub-policies to parents extracted in earlier chunks by setting
      parent_policy_name to the parent's name as shown in the summary.
    • Avoid re-extracting policies that already appear in the summary.
    • Understand the broader document context.

    DO NOT extract a policy if it clearly matches an entry in the summary.

    ═══════════════════════════════════════════════════════════════════════

    DEFINITION OF A POLICY
    A policy is a STATED COMMITMENT by a governing body to achieve a defined
    outcome through deliberate action, resource allocation, or regulatory change.

    A policy is NOT:
    - Background information or problem descriptions
    - Statements of current conditions
    - Aspirations without any specified action
    - Descriptions of what other actors might do

    WHAT MAKES SOMETHING EXTRACTABLE
    Extract a statement as a policy if it contains AT LEAST ONE of:

    1. QUANTIFIABLE TARGET: Numbers with units and/or deadlines
    2. BINDING MECHANISM: Legal or regulatory force
    3. SPECIFIC INTERVENTION: Named program, technology, or action
    4. RESOURCE ALLOCATION: Committed funding or investment

    DO NOT EXTRACT
    - Pure context or problem statements
    - Current state descriptions
    - Process descriptions without commitments
    - Vague aspirations without concrete anchors
    - Text from [OVERLAP CONTEXT] sections (use for context only)

    ═══════════════════════════════════════════════════════════════════════
    HIERARCHY CLASSIFICATION
    ═══════════════════════════════════════════════════════════════════════

    Determine policy_type based on DOCUMENT STRUCTURE:

    ┌─────────────────────────────────────────────────────────────────────┐
    │ PARENT POLICY                                                       │
    │                                                                     │
    │ A policy is 'parent' when:                                         │
    │ • It introduces an action/program/initiative with a name           │
    │ • Multiple specific sub-items are listed beneath it                │
    │ • Sub-items are labeled (A, B, C) or (1, 2, 3)                    │
    │ • The parent describes overarching goal/scope                      │
    │                                                                     │
    │ For parent policies:                                               │
    │   policy_type = "parent"                                           │
    │   parent_policy_name = None                                        │
    │   policy_statement = Summary of the overarching action             │
    │   verbatim_text = Introductory text for the action group           │
    └─────────────────────────────────────────────────────────────────────┘

    ┌─────────────────────────────────────────────────────────────────────┐
    │ SUB POLICY                                                          │
    │                                                                     │
    │ A policy is 'sub' when:                                            │
    │ • It appears as an item in a lettered/numbered list                │
    │ • Listed under a named parent action/initiative                    │
    │ • Labeled with A., B., C. or 1., 2., 3.                           │
    │                                                                     │
    │ For sub policies:                                                  │
    │   policy_type = "sub"                                              │
    │   parent_policy_name = Exact name of parent action                 │
    │   policy_statement = The specific sub-item commitment              │
    │   verbatim_text = Text of this specific list item                  │
    └─────────────────────────────────────────────────────────────────────┘

    ┌─────────────────────────────────────────────────────────────────────┐
    │ INDIVIDUAL POLICY                                                   │
    │                                                                     │
    │ A policy is 'individual' when:                                     │
    │ • It stands alone (not part of a parent-child list structure)      │
    │ • Has no lettered/numbered sub-items below it                      │
    │ • Not labeled A., B., C. under a parent                            │
    │                                                                     │
    │ For individual policies:                                           │
    │   policy_type = "individual"                                       │
    │   parent_policy_name = None                                        │
    └─────────────────────────────────────────────────────────────────────┘

    CLASSIFICATION DECISION TREE:

    Is this text labeled A., B., C. (or 1., 2., 3.) in a list?
      ├─ YES → Does a named action/initiative appear above it?
      │   ├─ YES → policy_type = "sub"
      │   │         parent_policy_name = [name of that action]
      │   │         ALSO extract the action header as a "parent" policy
      │   └─ NO  → policy_type = "individual"
      │
      └─ NO → Does this policy have lettered/numbered items below it?
          ├─ YES → policy_type = "parent"
          │         Extract each A/B/C below as "sub" policies
          └─ NO  → policy_type = "individual"

    CRITICAL RULES:
    • When you see A., B., C. lists, ALWAYS create both parent and sub policies
    • Extract the parent action/initiative as its own policy
    • Each list item (A, B, C) becomes a sub policy referencing the parent
    • The parent_policy_name should match the action name from section_header

    ═══════════════════════════════════════════════════════════════════════

    If no policies are found, return an empty list.
    """
    document_text: str = dspy.InputField(
        desc="Text extracted from a climate policy document (NDC, action plan, etc.)"
    )
    document_metadata: DocumentMetadata = dspy.InputField(
        desc="Document name, country, year, and any known section context"
    )
    prior_policies_summary: str = dspy.InputField(
        desc=(
            "Compact index of policies already extracted from earlier chunks. "
            "Use to link sub-policies to existing parents and avoid duplicates. "
            "Format: hierarchical list of parents, subs, and individuals. "
            "Empty string if this is the first chunk."
        ),
        default="",
    )

    policies: List[ExtractedPolicy] = dspy.OutputField(
        desc="List of extracted policies with correct hierarchy classification"
    )

class PolicyExtractor(dspy.Module):
    """
    Extract structured policy objects from document text via DSPy.

    Accepts an optional prior_policies_summary for cross-chunk context.
    """

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought(PolicyExtractionSignature)

    def forward(
        self,
        document_text: str,
        document_metadata: DocumentMetadata,
        prior_policies_summary: str = "",
    ) -> List[ExtractedPolicy]:
        result = self.extract(
            document_text=document_text,
            document_metadata=document_metadata,
            prior_policies_summary=prior_policies_summary,
        )
        return result.policies

