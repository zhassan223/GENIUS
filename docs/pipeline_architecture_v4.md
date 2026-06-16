# GENIUS Pipeline v4 — Architecture

## What it does

Extract, validate, and classify climate policies from government documents (NDCs, municipal action plans) across multiple cities. Every classification is traceable back to source text.

**Model:** `openai/gpt-5.2` via DSPy

---

## Pipeline Steps

**Step 1 — Load Markdown**
Priority: pre-converted markdown file → cached conversion → PDF conversion via Docling.

**Step 2 — Chunk & Extract**
`DocumentChunker` splits text into ~37,500-word chunks with 2-paragraph overlap. `ExtractionPipeline` runs `PolicyExtractor` (DSPy) on each chunk sequentially (carry-forward summary enables cross-chunk linking). `PolicyResolver` runs a 3-stage dedup → deterministic link → LLM arbitration pass to set `parent_policy_name`. Up to 4 retries per chunk with exponential backoff (2s–30s).

**Step 3 — Cluster**
`cluster_policies()` groups policies by `parent_policy_name` into `parent_with_subs`, `orphan_sub`, or `individual` clusters. Each cluster gets a sequential `cluster_id`.

**Step 4 — Flatten**
`clusters_to_records()` → DataFrame with uniform columns: `cluster_id`, `cluster_type`, `role`, `sector`, `section_header`, `policy_statement`, `parent_statement`, `verbatim_text`, `extraction_rationale`.

**Step 5 — Validate Individuals**
`PolicyValidator` (DSPy ChainOfThought) on each `individual`-role policy. Strict: needs target + timeline + binding mechanism + confidence ≥ 0.8. Output: `VALID` / `NON-SOUND`. Parallelized via `ParallelExecutor`.

**Step 6 — Validate Initiatives**
`InitiativeValidator` on each `parent_with_subs` cluster. Lenient: holistic cluster coverage, subs inherit parent context, 3-tier verdict (SOUND/PARTIAL/WEAK), confidence ≥ 0.55. Parallelized via `ParallelExecutor`.

**Step 7 — Export**
Per city: `combined_policies.csv`, `trace_individual_policies*.csv`, `trace_initiative_policies*.csv`, `policy_trace_lookup.csv`, `chunk_trace.json`.

**Step 8 — Classify**
`ConsistentPolicyClassifier` runs on all valid policies pooled across all cities:
1. **Mechanism extraction** (1 LLM call/policy, parallelized) → `canonical_mechanism`
2. **Mechanism canonicalization** (no LLM) → fuzzy-cluster mechanism strings at 0.85 threshold so wording variants collapse to one label
3. **Mechanism classification** (1 LLM call/unique mechanism) → `primary_category`, `secondary_categories`, `causal_pathway` — same mechanism always gets the same labels across all cities
4. **Policy enrichment** (1 LLM call/policy, parallelized) → `instrument_type`, `directness`, `climate_relevance`, location-based secondary labels (Stage 2 labels are locked)

Exports per city: `classified_policies.csv`, `combined_policies.csv`, `policy_traces/*.json`, `policy_trace_lookup.csv`, `excluded_policies_trace.csv`.

**Step 9 — Aggregate**
Concatenates all cities' `combined_policies.csv` and `classified_policies.csv` into a single cross-city DataFrame. Saves to repo root as `all_cities_kept_classified_policies_final.csv`.

---

## Component Dependencies

```
schemas.py              ← Pydantic models + make_doc_id
chunking.py             ← heading scorer, greedy bin-packing
dspy_extraction.py      ← depends on schemas, dspy
dspy_resolve.py         ← depends on schemas, dspy (3-stage resolver)
pipeline.py             ← orchestrates chunk → extract → resolve
clustering.py           ← groups by resolver's parent_policy_name
dspy_validation.py      ← individual policy validation
initiative_validator.py ← cluster-level validation
consistent_classification.py ← 3-stage classification
exports.py              ← standalone export functions
```

The notebook is the only place that wires all modules together.

---

## Configuration

| Parameter | Value | Location |
|-----------|-------|----------|
| `words_per_chunk` | 37,500 | Step 2 |
| `overlap_paragraphs` | 2 | `DocumentChunker` |
| `max_chunk_retries` | 4 | `ExtractionPipeline` |
| `NUM_THREADS` | 8 | Notebook |
| `verbatim_dedup_threshold` | 0.90 | `PolicyResolver` |
| `statement_dedup_threshold` | 0.85 | `PolicyResolver` |
| `link_threshold` | 0.85 | `PolicyResolver` |
| `mechanism_cluster_threshold` | 0.85 | `canonicalize_mechanisms` |

---

## Output Structure

```
outputs/{City}/
├── combined_policies.csv              ← final kept policies
├── classified_policies.csv            ← full classification metadata
├── policy_trace_lookup.csv            ← row → trace file mapping
├── policy_traces/*.json               ← one trace per kept policy
├── trace_individual_policies*.csv     ← individual validation traces
├── trace_initiative_policies*.csv     ← initiative validation traces
└── chunk_trace.json                   ← extraction provenance

all_cities_kept_classified_policies_final.csv  ← cross-city aggregate
```
