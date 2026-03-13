# Policy Output Access Guide

## Purpose

This guide explains how to read the final policy outputs produced by `notebooks/dspy_pipeline_v4.ipynb`, what each CSV means, and how to trace any final row back to its detailed reasoning.

The export structure is designed to give you:

- a minimal final CSV for presentation
- a per-policy JSON trace for classification details
- validator CSVs for soundness and initiative validation
- a lookup CSV that tells you exactly where to go for each row

## Output Folder Structure

Each city has its own output folder under:

`notebooks/outputs/<City>/`

Typical files in that folder are:

- `combined_policies.csv`
- `policy_trace_lookup.csv`
- `trace_individual_policies.csv`
- `trace_individual_policies_valid.csv`
- `trace_initiative_policies.csv`
- `trace_initiative_policies_valid.csv`
- `classified_policies.csv`
- `policy_traces/*.json`

## Main File To Present

The main presentation file is:

`combined_policies.csv`

This is the smallest final output and is intended to be the easiest file to browse, filter, and share.

## `combined_policies.csv` Columns

`combined_policies.csv` has these columns:

- `policy_id`
- `role`
- `parent_statement`
- `policy_statement`
- `primary_category`
- `trace_path`

### What Each Column Means

`policy_id`

- A stable row identifier within that city's final output.
- It matches the numeric prefix of the JSON file in `policy_traces/`.
- Example: `000` maps to `policy_traces/000_...json`

`role`

- Describes what kind of policy row this is.
- Possible values:
  - `individual`: a standalone policy
  - `parent`: a parent initiative or umbrella policy
  - `sub`: a sub-policy that belongs to a parent initiative

`parent_statement`

- The parent policy text for a `sub` row.
- Blank for `individual` and `parent` rows.
- Use this to understand which initiative a sub-policy belongs to.

`policy_statement`

- The actual policy text for that row.
- For a `parent` row, this is the initiative-level parent statement.
- For a `sub` row, this is the sub-action statement.
- For an `individual` row, this is the standalone policy statement.

`primary_category`

- The top-level climate classification for the row.
- Example values may include:
  - `Mitigation`
  - `Adaptation`
  - `Resource Efficiency`
  - `Nature-Based Solutions`

`trace_path`

- Relative path from the city output folder to the classification trace JSON.
- This file contains the detailed classification reasoning for that row.

## Example

Example row from `combined_policies.csv`:

```csv
policy_id,role,parent_statement,policy_statement,primary_category,trace_path
001,sub,"Overarching strategies to support achieving Austin’s net-zero by 2040 goal, including green jobs, prioritizing community initiatives, regional collaboration, and local carbon reduction/CDR/offsets guidance.","Create green jobs and entrepreneurship opportunities that advance the plan, expand economic opportunity and inclusion, and build decision-making power in low-income communities and communities of color.",Mitigation,policy_traces/001_Create_green_jobs_and_entrepreneurship_opportunities_that_ad.json
```

This tells you:

- the row is a `sub`
- it belongs to the parent named in `parent_statement`
- its main classification is `Mitigation`
- its detailed classification trace is in the JSON file given by `trace_path`

## `policy_trace_lookup.csv`

This file exists to make trace access explicit.

It contains these columns:

- `policy_id`
- `role`
- `parent_statement`
- `policy_statement`
- `primary_category`
- `trace_path`
- `validation_trace_csv`
- `validation_lookup_column`
- `validation_lookup_value`

### Why This File Exists

The JSON trace file gives you the classification reasoning for a row.

The validator trace may live in a different CSV depending on whether the row is:

- an `individual` policy
- a `parent` initiative
- a `sub` policy attached to a parent initiative

`policy_trace_lookup.csv` tells you exactly which validator file to open and what value to match.

## How To Access The Classification Trace

From a row in `combined_policies.csv`:

1. Read `trace_path`
2. Open the JSON file at that relative path inside the same city folder

Example:

- city folder: `notebooks/outputs/Dakar/`
- `trace_path`: `policy_traces/002_Increase_the_share_of_renewable_energy_in_Dakars_energy_mix_.json`
- full file: `notebooks/outputs/Dakar/policy_traces/002_Increase_the_share_of_renewable_energy_in_Dakars_energy_mix_.json`

## What The JSON Trace Contains

The per-policy JSON trace usually includes:

- `policy_statement`
- `role`
- `sector`
- `canonical_mechanism`
- `mechanism_description`
- `primary_category`
- `secondary_categories`
- `secondary_justification`
- `primary_causal_pathway`
- `causal_mechanism_detail`
- `dominant_pathway_test`
- `mechanism_classification_reasoning`
- `mechanism_confidence`
- `instrument_type`
- `instrument_directness`
- `climate_relevance`
- `key_indicators`
- `co_benefits`
- `instance_edge_case_notes`

This is the best file to open when you want to understand why a row got its classification.

## How To Access The Validation Trace

Validation traces are stored separately from the JSON classification traces.

Use `policy_trace_lookup.csv` to determine:

- which validator CSV to open
- which column to match on
- which value to match

### For `individual` Rows

Open:

- `trace_individual_policies_valid.csv`

Match on:

- column: `policy_statement`
- value: the row's `policy_statement`

This gives you the individual-policy validation result and reasoning.

### For `parent` Rows

Open:

- `trace_initiative_policies_valid.csv`

Match on:

- column: `parent_statement`
- value: the row's `policy_statement`

This works because the parent row itself is the initiative statement.

### For `sub` Rows

Open:

- `trace_initiative_policies_valid.csv`

Match on:

- column: `parent_statement`
- value: the row's `parent_statement`

This works because sub-policies inherit the initiative-level validation record from their parent cluster.

## Quick Lookup Rules

If you only need the short rule:

- `individual` -> JSON from `trace_path`, validator from `trace_individual_policies_valid.csv` matched on `policy_statement`
- `parent` -> JSON from `trace_path`, validator from `trace_initiative_policies_valid.csv` matched on the parent's own statement
- `sub` -> JSON from `trace_path`, validator from `trace_initiative_policies_valid.csv` matched on `parent_statement`

## What The Validator CSVs Mean

`trace_individual_policies.csv`

- Full validator output for individual policies
- Includes rows that may not have passed final validation

`trace_individual_policies_valid.csv`

- Only the individual policies with `final_verdict == True`

`trace_initiative_policies.csv`

- Full validator output for parent-plus-sub initiative clusters

`trace_initiative_policies_valid.csv`

- Only the initiative clusters with `final_verdict == True`

## What `classified_policies.csv` Means

`classified_policies.csv` is the broader classification output that contains the detailed classification fields for all classified policies in that city.

It is useful for analysis and debugging, but it is not the preferred presentation file.

Use:

- `combined_policies.csv` for presentation
- `policy_trace_lookup.csv` for navigation
- `policy_traces/*.json` for classification detail
- `trace_*_valid.csv` for validation detail

## Recommended Reading Workflow

If you want the cleanest workflow for one row:

1. Start in `combined_policies.csv`
2. Read `role`, `parent_statement`, `policy_statement`, and `primary_category`
3. Open the JSON file in `trace_path` for classification reasoning
4. Open `policy_trace_lookup.csv` if you want the exact validator mapping
5. Open the validator CSV listed in `validation_trace_csv`
6. Match using `validation_lookup_column` and `validation_lookup_value`

## Interpretation Notes

`individual`

- Treat as a standalone policy commitment

`parent`

- Treat as an initiative-level or umbrella statement that may group multiple sub-actions

`sub`

- Treat as a child action that belongs to the parent initiative named in `parent_statement`

`primary_category`

- This is the top-level climate label for the row, not the full reasoning

`trace_path`

- This points to classification reasoning, not validation reasoning

## Summary

Use `combined_policies.csv` when you want the final compact table.

Use `policy_trace_lookup.csv` when you want exact instructions for how to find the deeper trace for a row.

Use `policy_traces/*.json` when you want classification reasoning.

Use `trace_individual_policies_valid.csv` or `trace_initiative_policies_valid.csv` when you want the validator reasoning behind whether the row passed into the final output.

## Architecture

This output system sits on top of the main policy-processing notebook pipeline.

At a high level, the architecture has two layers:

- the core policy pipeline
- the final presentation and trace-access layer

### Core Policy Pipeline

The core notebook pipeline is responsible for turning source documents into validated, classified policy records.

The main stages are:

1. Load city documents from Markdown or PDF
2. Extract policy candidates with DSPy
3. Recover structure by grouping policies into parent/sub/individual clusters
4. Flatten those clusters into row-like policy records
5. Validate standalone individual policies
6. Validate parent-plus-sub clusters as initiatives
7. Build the validated combined policy table
8. Classify valid policies using the mechanism registry pipeline

In practical terms, the core pipeline produces the structural and analytical truth of the system:

- which rows are `individual`, `parent`, or `sub`
- which rows passed validation
- what the final `primary_category` and related classification fields are

### Core Pipeline Components

#### 1. Source Layer

Inputs come from:

- source Markdown files
- source PDF files
- the notebook `BATCH` configuration

Each city enters the system as a document plus metadata.

#### 2. Document Ingestion Layer

The notebook first loads the city document as Markdown.

It prefers:

1. a configured Markdown path
2. a cached Markdown file from an earlier run
3. live PDF-to-Markdown conversion

The output of this stage is one Markdown string per city.

#### 3. Policy Extraction Layer

The Markdown document is passed to the DSPy `PolicyExtractor`.

This step extracts candidate policy objects such as:

- `policy_statement`
- `verbatim_text`
- `policy_type`
- `parent_policy_name`
- `section_header`
- `sector`
- `extraction_rationale`

This stage is where free-text planning documents first become structured policy candidates.

#### 4. Structural Clustering Layer

The extracted policies are then grouped into structural clusters.

Cluster types are:

- `parent_with_subs`
- `individual`
- `orphan_sub`

This is the part of the architecture that reconstructs hierarchy from the extracted rows.

It determines whether a statement should be treated as:

- a parent initiative
- a child action under a parent
- a standalone policy

#### 5. Policy Record Normalization Layer

The cluster objects are flattened into row-level records.

This produces a table-shaped representation where each row has fields such as:

- `role`
- `policy_statement`
- `parent_statement`
- `section_header`
- `sector`
- `verbatim_text`
- `extraction_rationale`

This layer is important because it converts nested structure into a consistent row model that later steps can validate and classify.

#### 6. Validation Layer

Validation splits into two branches.

Branch A: individual validation

- validates rows where `role == individual`
- writes individual validation traces

Branch B: initiative validation

- validates `parent_with_subs` clusters as whole initiatives
- writes initiative validation traces

This is where the pipeline determines which rows or clusters are strong enough to count as valid final policies.

#### 7. Combined Valid Policy Layer

The notebook merges structural records with validation verdicts.

This creates:

- a combined structural table
- a filtered valid-only table

At this stage, the system knows:

- which rows are final
- which rows are parent vs sub vs individual
- which parent a sub row belongs to

#### 8. Classification Layer

Only valid rows move into classification.

Classification itself has three parts:

1. mechanism extraction
2. deduplicated mechanism classification
3. per-policy enrichment

This stage produces the semantic climate labels such as:

- `primary_category`
- `secondary_categories`
- `canonical_mechanism`
- `instrument_type`
- `climate_relevance`
- `co_benefits`

This is the stage that creates the detailed classification traces later exposed through `policy_traces/*.json`.

### New Presentation And Trace-Access Layer

The new addition sits after classification.

Its job is not to decide what the policy means. Its job is to make the final outputs easier to present and easier to audit.

This layer takes:

- the validated combined rows
- the classified policy rows
- the validator trace CSVs already written by earlier steps

Then it produces:

- a minimal presentation CSV
- a trace lookup CSV
- one per-policy classification trace JSON file

### What Is New In This Layer

The new architecture introduces three export behaviors:

#### 1. Minimal Final Presentation Output

The final `combined_policies.csv` is rewritten into a presentation-friendly schema with only:

- `policy_id`
- `role`
- `parent_statement`
- `policy_statement`
- `primary_category`
- `trace_path`

This is the main file intended for browsing and presenting findings.

#### 2. Stable Trace Identity

Each classified policy row is assigned a stable `policy_id`.

That `policy_id` is used to build the JSON filename under `policy_traces/`.

This creates a consistent mapping between:

- the row in `combined_policies.csv`
- the row in `policy_trace_lookup.csv`
- the JSON file in `policy_traces/`

#### 3. Explicit Trace Navigation

The new `policy_trace_lookup.csv` acts as a routing table.

For each final row, it tells you:

- which classification trace JSON to open
- which validator CSV to open
- which field to match in that validator CSV
- which value to match

This is the main architectural addition that makes trace access easy and deterministic instead of implicit.

### Trace Routing Logic

The trace routing logic depends on `role`.

For `individual` rows:

- classification trace: use `trace_path`
- validation trace CSV: `trace_individual_policies_valid.csv`
- validator match field: `policy_statement`

For `parent` rows:

- classification trace: use `trace_path`
- validation trace CSV: `trace_initiative_policies_valid.csv`
- validator match field: `parent_statement`
- validator match value: the parent row's own `policy_statement`

For `sub` rows:

- classification trace: use `trace_path`
- validation trace CSV: `trace_initiative_policies_valid.csv`
- validator match field: `parent_statement`
- validator match value: the sub row's `parent_statement`

This means:

- every final row has its own classification trace JSON
- `parent` and `sub` rows share the initiative-level validation record through the parent statement
- `individual` rows have their own policy-level validation record

### Architecture As A Flow

The architecture can be summarized as:

1. Source document
2. Extraction
3. Hierarchy reconstruction
4. Validation
5. Classification
6. Final export and trace-access packaging

Or more explicitly:

1. city documents become extracted policy candidates
2. extracted candidates become structured parent/sub/individual clusters
3. clustered records are validated
4. valid records are classified
5. classified records are merged with final valid rows
6. the final export layer writes:
   - `combined_policies.csv`
   - `policy_trace_lookup.csv`
   - `policy_traces/*.json`

### What To Show In A System Diagram

If you are drawing a system diagram, the most important boxes are:

- Source documents
- Markdown loader / PDF conversion
- Policy extractor
- Cluster builder
- Policy record normalizer
- Individual validator
- Initiative validator
- Combined valid policy builder
- Mechanism classification pipeline
- New presentation and trace-access layer

The most important outputs to show are:

- `combined_policies.csv`
- `policy_trace_lookup.csv`
- `policy_traces/*.json`
- `trace_individual_policies_valid.csv`
- `trace_initiative_policies_valid.csv`
- `classified_policies.csv`

### What Is Architecturally New

If you want to visually highlight the new addition in a diagram, highlight this final export block:

- `classified_policies.csv` plus valid combined rows go into the new export helper
- the helper generates stable `policy_id`s
- it writes per-policy JSON traces
- it writes the minimal final `combined_policies.csv`
- it writes `policy_trace_lookup.csv` to explain how to reach the validation traces

That final block is the new system layer added on top of the existing extraction, validation, and classification pipeline.
