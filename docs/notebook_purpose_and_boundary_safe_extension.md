# `dspy_pipeline_v4.ipynb` Purpose and Boundary-Safe Extension

## Notebook Purpose

`notebooks/dspy_pipeline_v4.ipynb` is the main orchestration notebook for the GENIUS climate policy pipeline.

Its purpose is to take climate policy source documents for multiple cities, convert or load them as Markdown, extract actionable policies, validate which ones are strong enough to keep, and classify the valid policies into a consistent mechanism-based taxonomy.

In practical terms, the notebook does the following:

1. Loads each city's source document from either a pre-converted Markdown file or a cached/generated Markdown version.
2. Sends the document text to a DSPy-based `PolicyExtractor` to identify candidate policies.
3. Groups extracted policies into structural clusters such as:
   - parent policies with sub-policies
   - standalone individual policies
   - orphaned sub-policies
4. Validates:
   - individual policies on their own
   - parent-plus-sub clusters as initiatives
5. Builds combined output tables containing the policies that passed validation.
6. Runs a three-stage classification pipeline so similar policies across cities receive consistent labels.
7. Writes audit-friendly outputs such as:
   - `combined_policies.csv`
   - `classified_policies.csv`
   - validation trace files
   - classification trace files

## Why This Notebook Exists

The notebook exists to turn long, messy, human-written climate plans into structured, comparable policy records that can be analyzed across cities.

It is not just an extraction notebook. It is the current end-to-end policy processing pipeline for:

- ingestion
- extraction
- structural grouping
- validation
- classification
- export

That makes it the core place where document text becomes reusable policy data.

## Current Design Assumption

The current notebook is built around a strong assumption: each city's document can be treated as one large Markdown string during extraction.

That assumption works reasonably well when the document fits comfortably in one model call, because the model can see:

- the section header
- the parent initiative description
- the child list items below it
- nearby context that helps decide whether something is a parent, sub-policy, or individual policy

This is why the current pipeline can recover parent/sub structure at all.

## Current Limitation

The main limitation is that hierarchy recovery depends too heavily on a single extraction pass over one large block of text.

If a document becomes too large and has to be split, the current pipeline can lose relationships such as:

- a parent initiative appearing in chunk A
- its sub-actions appearing in chunk B
- list continuity across chunk boundaries
- heading context that ties related policies together

When that happens, true sub-policies may be misread as standalone policies or orphaned children, which then weakens:

- initiative validation
- combined outputs
- final classification quality

## Goal Of The New Addition

The goal of the new addition is to make the notebook pipeline boundary-safe.

That means the pipeline should be able to split a very large Markdown document into large semantic chunks while still preserving the relationships inside that document, especially:

- parent to child policy links
- heading ancestry
- list continuation across chunk boundaries
- source order
- document-level provenance

The new addition is not meant to replace the notebook's purpose. It is meant to preserve that purpose when documents are too large for the current one-shot extraction design.

More specifically, the new addition should change where structural truth comes from:

- today, parent/child structure is inferred directly from one large extraction pass
- after the addition, chunk-local extraction should be treated as provisional
- the final parent/child structure should only be trusted after document-level reconciliation

## What The New Addition Should Achieve

The new addition should introduce a preprocessing and reconciliation layer around the existing pipeline so that:

1. A Markdown document is first parsed into structural blocks such as headings, paragraphs, and list items.
2. Those blocks are grouped into large semantic chunks instead of arbitrary text slices.
3. Each chunk carries boundary context, such as the active heading path and any open parent initiative or open list state.
4. Extraction runs on each chunk with enough inherited context to avoid losing parent/sub relationships.
5. A document-level reconciliation pass merges chunk-local results back into one coherent policy tree for that document.
6. The existing downstream steps, especially initiative validation and mechanism classification, operate on reconciled document-level policies rather than fragmented chunk-local guesses.

## Authoritative Source Of Truth

The most important design principle is that semantic chunks are only an extraction unit. They are not the authoritative source of policy structure.

The authoritative source of truth should become the combination of:

- block-level provenance from the parsed Markdown
- boundary state passed across chunk splits
- document-level reconciliation logic

That means:

- chunk-local extraction may suggest candidate parent/sub relationships
- reconciliation decides which relationships are accepted
- downstream validation and classification should run only on reconciled document-level objects

## Where The Real Difficulty Is

The hard part of this addition is not chunking by itself. The hard part is reconciliation.

If a document is split and:

- a parent initiative appears in chunk A
- some children appear in chunk B
- the extractor phrases the parent slightly differently in each chunk

then the system must still decide whether those chunk-local outputs refer to the same underlying initiative.

This means the new layer has to do more than preserve context. It has to resolve structure.

## Ambiguity And Failure Modes

The pipeline should not assume that every split can be resolved cleanly.

Some boundary cases will remain ambiguous, for example:

- two parent candidates with similar wording but unclear identity
- a child policy whose likely parent is missing or weakly inferred
- malformed Markdown that weakens heading or list structure

In those cases, the pipeline should prefer explicit ambiguity over false certainty.

That means:

- unresolved cases should be marked as ambiguous rather than force-attached
- chunk-level extraction errors should be traceable
- reconciliation conflicts should be visible in outputs or audit traces

This is important because a wrongly reconstructed initiative can mislead every downstream stage, especially initiative validation.

## Scope Of Complexity

Because the intended chunk size is large, many documents may still fit in a single chunk.

That is a good thing.

It means:

- the current extraction behavior remains close to today's behavior for many documents
- the boundary-safe logic activates mainly for the largest or structurally hardest documents
- reconciliation complexity is applied where needed, not everywhere

So the new addition should be designed as a targeted robustness layer, not as a complete replacement of the current pipeline behavior for every document.

## Intended Outcome

Once this addition is in place, the notebook should still produce the same kind of outputs as today, but it should do so more reliably for large documents.

The intended outcome is:

- large documents can be split safely
- parent/sub structures survive chunk boundaries
- orphaned sub-policies are reduced
- initiative-level validation becomes more faithful to the original document
- cross-city classification improves because the classifier receives cleaner, reconciled policy records

Just as importantly:

- chunk-local mistakes do not silently become final structure
- unresolved edge cases remain auditable
- the pipeline becomes more trustworthy, not just more scalable

## Final Verdict

This is a strong direction for the notebook, but only if the new addition is treated as a structural reliability layer rather than a simple chunking upgrade.

The strongest part of the approach is the overall idea:

- keep large semantic chunks
- preserve boundary context
- reconcile globally at the document level

That is the right architecture for this pipeline.

The main risk is over-trusting chunk-local extraction or over-promising what chunk metadata alone can solve. Chunking helps, but reconciliation is what makes the system correct.

So the final verdict is:

- the approach is good
- the rationale is sound
- the success of the design will depend mostly on explicit, auditable, document-level reconciliation

If that reconciliation layer is implemented carefully, this addition should make the notebook much more robust for large Markdown documents without changing its core purpose.

## Short Summary

`dspy_pipeline_v4.ipynb` is the current end-to-end climate policy extraction, validation, and classification notebook for GENIUS.

The new addition is meant to extend that pipeline so it can handle very large Markdown documents without breaking the structural relationships that the rest of the pipeline depends on.
