# GENIUS Pipeline v4 — Architecture Document

## System Purpose

Extract, validate, and classify climate policies from government documents (NDCs, municipal action plans, climate strategies) across 10+ cities/countries. Produce structured, traceable outputs where every classification decision can be audited back to source text.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DOCUMENT INGESTION                           │
│                                                                     │
│  PDF / Markdown ──► Docling conversion ──► markdowns dict           │
│                                            {city_key: text}         │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    STEP 1: LOAD & IDENTIFY                          │
│                                                                     │
│  For each city:                                                     │
│    DocumentMetadata ──► make_doc_id(meta, text) ──► stable doc_id   │
│                                                                     │
│  doc_id = slug(city_country) + md5(whitespace-normalized text)[:6]  │
│                                                                     │
│  Input:  BATCH config + raw markdown text                           │
│  Output: markdowns dict with doc_id-tagged metadata                 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                STEP 2: EXTRACT (per city)                            │
│                                                                     │
│  ExtractionPipeline.run(text, metadata) → PipelineResult            │
│                                                                     │
│  ┌─────────────┐   ┌──────────────────┐   ┌──────────────────┐     │
│  │ Document     │   │ PolicyExtractor  │   │ PolicyResolver   │     │
│  │ Chunker      │   │ (DSPy module)    │   │ (3-stage)        │     │
│  │              │   │                  │   │                  │     │
│  │ • Heading    │   │ • Single-chunk   │   │ Stage 1: Dedup   │     │
│  │   scorer     │──►│   extraction     │──►│  (verbatim +     │     │
│  │ • Greedy     │   │ • Overlap guard  │   │   statement)     │     │
│  │   bin-pack   │   │ • Prior summary  │   │ Stage 2: Link    │     │
│  │ • Overlap    │   │ • Retry w/       │   │  (3-field fuzzy) │     │
│  │   window     │   │   backoff        │   │ Stage 3: LLM     │     │
│  │              │   │ • Post-extract   │   │  arbitration     │     │
│  │              │   │   normalization  │   │  (retry + per-   │     │
│  │              │   │                  │   │   entry parsing) │     │
│  └─────────────┘   └──────────────────┘   └──────────────────┘     │
│                                                                     │
│  Defensive layers:                                                  │
│    • _normalize_policies(): lowercase type, strip fields, reject    │
│      empty statement/verbatim                                       │
│    • Chunk retry with exponential backoff (max_chunk_retries)       │
│    • Resolver always runs (even single-chunk docs)                  │
│    • Orphan count detects dangling parent_policy_name refs          │
│                                                                     │
│  Outputs per city:                                                  │
│    • List[ExtractedPolicy]     (resolved, parent_policy_name set)   │
│    • chunk_provenance          (policy_statement → chunk_index)     │
│    • chunk_results             (per-chunk metrics)                  │
│    • resolver_stats            (dedup/link/arbitrate/orphan counts) │
│    • chunk_trace.json          (full extraction trace)              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  STEP 3: CLUSTER (deterministic)                    │
│                                                                     │
│  cluster_policies(policies) → List[cluster_dict]                    │
│                                                                     │
│  Uses RESOLVER LINKAGE (parent_policy_name), not section_header:    │
│    • Index parents by policy_statement                              │
│    • Match sub.parent_policy_name → parent.policy_statement         │
│    • Matched subs → "parent_with_subs" cluster                     │
│    • Unmatched subs → "orphan_sub" cluster                          │
│    • Individuals → "individual" cluster                              │
│    • Each cluster gets a stable cluster_id (sequential int)         │
│                                                                     │
│  Key invariant: Resolver is the single source of truth for          │
│  parent-sub linkage. Clustering never re-derives relationships.     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    STEP 4: FLATTEN TO RECORDS                       │
│                                                                     │
│  clusters_to_records(clusters) → List[dict]                         │
│  → pd.DataFrame with uniform columns:                               │
│    cluster_id, cluster_type, role, sector, section_header,          │
│    policy_statement, parent_statement, verbatim_text,               │
│    extraction_rationale                                              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                     ┌─────────┴─────────┐
                     │                   │
                     ▼                   ▼
┌────────────────────────┐  ┌────────────────────────────┐
│  STEP 5: VALIDATE      │  │  STEP 6: VALIDATE          │
│  INDIVIDUALS           │  │  INITIATIVES               │
│  (parallelized)        │  │  (parallelized)            │
│                        │  │                            │
│  PolicyValidator       │  │  InitiativeValidator       │
│  (DSPy ChainOfThought) │  │  (DSPy ChainOfThought)    │
│                        │  │                            │
│  Strict criteria:      │  │  Lenient criteria:         │
│  • Target + timeline   │  │  • Cluster-level coverage  │
│  • Binding mechanism   │  │  • Subs inherit parent     │
│  • Strong language     │  │    context                 │
│  • Confidence ≥ 0.8    │  │  • 3-tier: SOUND/PARTIAL/  │
│                        │  │    WEAK                    │
│  Binary: VALID/        │  │  • Confidence ≥ 0.55       │
│          NON-SOUND     │  │                            │
│                        │  │  Per-sub assessments:      │
│  Output:               │  │  strong/moderate/weak      │
│  ValidationMetrics     │  │                            │
│  + final_verdict bool  │  │  Output:                   │
│                        │  │  InitiativeMetrics         │
│                        │  │  + final_verdict bool      │
└───────────┬────────────┘  └──────────────┬─────────────┘
            │                              │
            └──────────┬───────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    STEP 7: EXPORT COMBINED + TRACES                 │
│                                                                     │
│  Outputs per city:                                                  │
│    combined_policies.csv           ← valid policies only            │
│    trace_individual_policies.csv   ← all individual validation      │
│    trace_individual_policies_valid.csv                               │
│    trace_initiative_policies.csv   ← all initiative validation      │
│    trace_initiative_policies_valid.csv                               │
│    policy_trace_lookup.csv         ← row → trace file mapping       │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│         STEP 8: THREE-STAGE MECHANISM CLASSIFICATION                │
│         (pools ALL valid policies across ALL cities)                │
│                                                                     │
│  ConsistentPolicyClassifier                                         │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────┐      │
│  │ STAGE 1: Mechanism Extraction (parallelized, 1 call/policy)│     │
│  │                                                           │      │
│  │ Input:  policy_statement + verbatim_text                  │      │
│  │ Output: canonical_mechanism (normalized causal chain)     │      │
│  │         e.g. "waste_diversion → landfill_methane_avoid"   │      │
│  │                                                           │      │
│  │ Normalization: strip city, targets, dates — keep causal   │      │
│  │ chain, sector, instrument class                           │      │
│  └───────────────────────────┬───────────────────────────────┘      │
│                              │                                      │
│                              ▼                                      │
│  ┌───────────────────────────────────────────────────────────┐      │
│  │ STAGE 1.5: Mechanism Canonicalization (no LLM)            │      │
│  │                                                           │      │
│  │ canonicalize_mechanisms() — fuzzy-cluster variants:       │      │
│  │   • Normalize: lowercase, collapse separators, unify →/-> │      │
│  │   • Greedy single-linkage clustering (threshold 0.85)     │      │
│  │   • Merge near-identical strings to one canonical form    │      │
│  │   • Stores _mechanism_key on each policy dict             │      │
│  │   • Prevents label drift from LLM string inconsistency   │      │
│  └───────────────────────────┬───────────────────────────────┘      │
│                              │                                      │
│                              ▼                                      │
│  ┌───────────────────────────────────────────────────────────┐      │
│  │ STAGE 2: Mechanism Classification (1 call/unique mechanism)│     │
│  │                                                           │      │
│  │ Group by _mechanism_key (normalized) → classify ONCE      │      │
│  │ Sees 1-3 representative policies from different cities    │      │
│  │                                                           │      │
│  │ Output: primary_category, secondary_categories,           │      │
│  │         causal_pathway, confidence, reasoning             │      │
│  │                                                           │      │
│  │ Categories: Mitigation | Adaptation | Resource Efficiency │      │
│  │             | Nature-Based Solutions                      │      │
│  │                                                           │      │
│  │ CONSISTENCY GUARANTEE: Same mechanism → same labels       │      │
│  │ across all cities                                         │      │
│  └───────────────────────────┬───────────────────────────────┘      │
│                              │                                      │
│                              ▼                                      │
│  ┌───────────────────────────────────────────────────────────┐      │
│  │ STAGE 3: Policy Enrichment (parallelized, 1 call/policy)  │     │
│  │                                                           │      │
│  │ Pre-filled: Stage 2 labels (LOCKED, cannot change)        │      │
│  │ Adds: instrument_type, directness, climate_relevance,     │      │
│  │       location-based secondaries, co-benefits             │      │
│  │                                                           │      │
│  │ Location vulnerability context (LOCATION_VULNERABILITIES) │      │
│  │ → can ADD Adaptation secondary for local climate hazards  │      │
│  │   but cannot remove/change mechanism-level labels         │      │
│  │                                                           │      │
│  │ build_vulnerability_context() normalizes city keys:       │      │
│  │   "Miami-Dade" → "Miami_Dade" (hyphens/spaces → _)       │      │
│  └───────────────────────────┬───────────────────────────────┘      │
│                              │                                      │
│  Outputs per city:                                                  │
│    classified_policies.csv         ← full classification metadata   │
│    policy_traces/*.json            ← one trace per valid policy     │
│    combined_policies.csv           ← final presentation table       │
│    policy_trace_lookup.csv         ← updated with classification    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Component Dependency Graph

```
schemas.py          ← no dependencies (Pydantic models + make_doc_id)
    ▲
    │
chunking.py         ← no dependencies (pure Python: heading scorer, bin-packing)
    ▲
    │
dspy_extraction.py  ← depends on: schemas, dspy
    ▲
    │
dspy_resolve.py     ← depends on: schemas, dspy, re, json, time
    ▲                  (3-stage: dedup + link + LLM arbitration)
    │
pipeline.py         ← depends on: chunking, dspy_extraction, dspy_resolve, schemas
    ▲                  (orchestrates: chunk → extract → normalize → resolve)
    │
clustering.py       ← depends on: schemas
    ▲                  (groups by resolver's parent_policy_name linkage)
    │
dspy_validation.py  ← depends on: schemas, dspy
    ▲
    │
initiative_validator.py ← depends on: schemas, dspy (shim → notebooks/ module)
    ▲
    │
consistent_classification.py ← depends on: dspy, re, difflib
    ▲                           (3-stage: extract → canonicalize → classify → enrich)
    │
exports.py          ← depends on: pandas (standalone export functions)
```

**Key design principles:**
- Each module has minimal dependencies and can be tested independently
- The resolver is the single source of truth for parent-sub linkage
- Clustering reads linkage; it never re-derives it
- The notebook is the only place that wires everything together

---

## Data Flow Summary

| Step | Input | Output | LLM Calls | Parallelized |
|------|-------|--------|-----------|--------------|
| 1. Load | PDF/Markdown | markdowns dict | 0 | No |
| 2. Extract | text + metadata | PipelineResult | 1/chunk (+ retries) + 0-1 arbitration | Per-city |
| 3. Cluster | policies | cluster list | 0 | No |
| 4. Flatten | clusters | DataFrame | 0 | No |
| 5. Validate individuals | individual rows | ValidationMetrics | 1/policy | Yes (8 threads) |
| 6. Validate initiatives | parent_with_subs | InitiativeMetrics | 1/cluster | Yes (8 threads) |
| 7. Export | DataFrames | CSV + lookup | 0 | No |
| 8a. Mechanism extraction | valid policies | canonical mechanisms | 1/policy | Yes (8 threads) |
| 8a.5. Canonicalize | mechanism strings | clustered mechanisms | 0 | No |
| 8b. Mechanism classification | unique mechanisms | labels per mechanism | 1/mechanism | Sequential |
| 8c. Policy enrichment | policies + labels | enriched metadata | 1/policy | Yes (8 threads) |

---

## Trace Architecture

Every policy in the final output can be traced back through:

```
combined_policies.csv
    │
    ├─► policy_trace_lookup.csv
    │       Maps each row to:
    │         • Its classification JSON trace file
    │         • Its validation CSV and lookup key
    │
    ├─► policy_traces/{id}_{slug}.json
    │       Full classification trace:
    │         mechanism, causal pathway, primary/secondary,
    │         instrument type, confidence, reasoning
    │
    ├─► trace_individual_policies_valid.csv
    │       Individual validation metrics:
    │         target, timeline, binding mechanism, confidence,
    │         reasoning, final_verdict
    │
    ├─► trace_initiative_policies_valid.csv
    │       Initiative validation metrics:
    │         coverage, coherence, per-sub assessments,
    │         initiative_result, confidence, reasoning
    │
    └─► chunk_trace.json
            Extraction provenance:
              chunk_index per policy, resolver stats,
              per-chunk metrics (word count, time, errors)
```

---

## Key Design Decisions

### 1. Sequential chunk extraction (not parallel)

Chunks are processed sequentially because each chunk's carry-forward summary depends on all prior chunks' results. This is intentional — parallel extraction would lose cross-chunk linking context.

### 2. Resolver is the single source of truth for parent-sub linkage

The three-stage resolver (dedup → deterministic link → LLM arbitration) sets `parent_policy_name` on every sub-policy. Downstream components (clustering, exports, validation) read this field directly. No component re-derives parent-sub relationships.

### 3. Mechanism-level classification (not policy-level)

Stage 2 of classification classifies each *unique mechanism* once, then propagates labels. This guarantees that "solar PV deployment" gets the same labels in Austin, Chicago, and Hiroshima. Stage 1.5 fuzzy-clusters mechanism string variants (threshold 0.85) so LLM wording inconsistency doesn't break this guarantee.

### 4. Two-tier validation (strict individuals, lenient initiatives)

Individual policies need target + timeline + binding mechanism (strict). But initiative clusters are evaluated holistically — a weak sub-action is acceptable if the cluster has strong anchors. This reflects how real climate action plans work.

### 5. Resolver always runs (even single-chunk documents)

Ensures consistent dedup and orphan logging regardless of document size. The resolver is a no-op for clean single-chunk extractions, so the cost is negligible.

### 6. Carry-forward summary is template-only (no LLM)

The summary uses string formatting at ~25 tokens per entry. This avoids an extra LLM call per chunk while providing enough signal for cross-chunk linking. Matching uses fuzzy similarity (threshold 0.80), not substring containment.

### 7. Location vulnerability context is populated and normalized

All 10 cities have climate hazard entries. City key lookup normalizes hyphens/spaces to underscores so formatting differences don't silently disable the enrichment pathway.

---

## Defensive Layers

| Layer | Location | What It Catches |
|-------|----------|----------------|
| Post-extraction normalization | `pipeline.py` | Malformed policy_type, empty statements, whitespace |
| Chunk retry with backoff | `pipeline.py` | Transient LLM failures per chunk |
| Verbatim + statement dedup | `dspy_resolve.py` | Overlap-induced near-duplicates |
| 3-field deterministic link | `dspy_resolve.py` | Cross-chunk parent-sub matching |
| LLM arbitration retry + per-entry parsing | `dspy_resolve.py` | Malformed JSON, "null" strings, invalid indices |
| Dangling parent ref detection | `dspy_resolve.py` | Subs pointing to non-existent parents |
| Section header normalization | `clustering.py` | Whitespace/case/punctuation variants (display only) |
| Mechanism string canonicalization | `consistent_classification.py` | Arrow notation variants, near-identical mechanism strings |
| Normalized registry keying | `consistent_classification.py` | Registry uses `_mechanism_key` (serialization-safe) not raw strings |
| Vulnerability key normalization | `consistent_classification.py` | Hyphen/space/underscore/case formatting in city keys |
| Stable cluster IDs | `clustering.py` | Every cluster dict has a `cluster_id` (sequential int) |
| Cluster backfill | `exports.py` | Validated policies missing from cluster walk |

---

## Configuration Points

| Parameter | Location | Default | Purpose |
|-----------|----------|---------|---------|
| `WORDS_PER_CHUNK` | Notebook | 6000 | Word count per chunk (set conservatively to leave room for carry-forward summary) |
| `NUM_THREADS` | Notebook | 8 | Parallel extraction/validation threads |
| `HeadingWeights.*` | chunking.py | See dataclass | Heading scorer tuning |
| `overlap_paragraphs` | DocumentChunker | 2 | Overlap window size |
| `max_chunk_retries` | ExtractionPipeline | 1 | Retry attempts per failed chunk |
| `verbatim_dedup_threshold` | PolicyResolver | 0.90 | Verbatim similarity for dedup |
| `statement_dedup_threshold` | PolicyResolver | 0.85 | Statement similarity for dedup |
| `link_threshold` | PolicyResolver | 0.85 | Fuzzy match for parent linking |
| `mechanism_cluster_threshold` | canonicalize_mechanisms | 0.85 | Fuzzy match for mechanism string merging |
| `LOCATION_VULNERABILITIES` | consistent_classification.py | 10 cities | Per-city climate hazards for Stage 3 enrichment |

---

## Output Directory Structure

```
outputs/{City}/
├── combined_policies.csv              ← Final presentation table
├── policy_trace_lookup.csv            ← Row → trace mapping
├── policy_traces/                     ← Per-policy classification JSON
│   ├── 000_Electrify_municipal_fleet.json
│   ├── 001_Building_energy_code.json
│   └── ...
├── classified_policies.csv            ← Full classification metadata
├── trace_individual_policies.csv      ← All individual validation
├── trace_individual_policies_valid.csv
├── trace_initiative_policies.csv      ← All initiative validation
├── trace_initiative_policies_valid.csv
└── chunk_trace.json                   ← Extraction provenance
```
