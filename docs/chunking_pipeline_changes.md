# Smart Chunking Pipeline — Change Documentation

## Overview

The v4 pipeline previously sent entire documents (some 80k+ words) in a single LLM call for policy extraction. This set of changes introduces a **chunking layer** that splits documents into LLM-friendly segments while preserving cross-chunk context through a carry-forward summary and a three-stage post-extraction resolver.

Nine architectural issues were identified and fixed during implementation.

---

## Changes by File

### `notebooks/utils/schemas.py` — Modified

**What changed:**
- Added `doc_id: Optional[str]` field to `DocumentMetadata`
- Added `make_doc_id(metadata, text)` function

**Why:**
Every document needs a stable identifier for trace files and logging. The ID is a slug (city + country) plus a 6-char content hash. **Fix #7** ensures the hash is stable across whitespace reformatting by normalizing all whitespace to single spaces before hashing — so re-exporting a PDF with slightly different line breaks won't change the doc_id and break trace continuity.

---

### `notebooks/utils/dspy_extraction.py` — Modified

**What changed:**
- Added `[OVERLAP CONTEXT]` extraction guard to signature docstring
- Added `prior_policies_summary` input field to `PolicyExtractionSignature`
- Updated `PolicyExtractor.forward()` to accept and pass `prior_policies_summary`

**Why (Fix #2a):**
When chunks overlap (last 2 paragraphs of chunk N prepended to chunk N+1), the LLM would extract policies from the overlap text *again*, creating near-duplicates. The signature now explicitly instructs the model: "DO NOT extract any policies from overlap sections. Overlap text exists solely to help you understand context for policies in the NEW text that follows."

The `prior_policies_summary` field gives the extractor visibility into what was already extracted from earlier chunks, enabling it to:
- Link sub-policies to parents from prior chunks
- Skip policies it already extracted
- Understand the broader document structure

---

### `notebooks/utils/chunking.py` — New File

**Components:**
- `HeadingWeights` dataclass (Fix #6)
- `Chunk` dataclass
- `DocumentChunker` class

**Architecture:**

```
Document text
    │
    ▼
Unified heading scorer (one pass, weighted features)
    │
    ▼
Section splitting (by detected headings)
    │
    ▼
Greedy bin-packing (sections into bins up to budget)
    │
    ├─ Oversized section? → Paragraph fallback
    │
    ▼
Overlap window (last N paragraphs of chunk N → prefix of chunk N+1)
    │
    ▼
List[Chunk] with metadata
```

**Fix #6 — Configurable heading scorer:**
The original plan described four separate heuristics ("starts with #", "short and uppercase", etc.) as a cascade. This was replaced with a single unified scorer where each feature has a configurable weight. One `HeadingWeights` dataclass controls all tuning — no need to maintain four detectors. Different document types (NDCs vs. municipal action plans) can pass different weights.

**Adaptive budget:**
The effective chunk size shrinks as the carry-forward summary grows. Early chunks (few prior policies) get the full budget; later chunks automatically shrink to leave room for the summary in the context window. `MIN_CHUNK_WORDS = 800` prevents degenerate single-paragraph chunks.

---

### `notebooks/utils/dspy_resolve.py` — New File

**Components:**
- `ResolverStats` dataclass (Fix #9)
- `ResolverResult` dataclass (Fix #9)
- `ParentSubArbitrationSignature` (DSPy signature for Stage 3)
- `PolicyResolver` class

**Three-stage resolution:**

| Stage | Method | Cost | Purpose |
|-------|--------|------|---------|
| 1a | Verbatim dedup (threshold 0.90) | Zero LLM | Remove near-duplicate extractions |
| 1b | Statement dedup (threshold 0.85) | Zero LLM | Catch overlap-induced dupes (Fix #2b) |
| 2 | Deterministic link | Zero LLM | Match subs to parents via fuzzy string matching |
| 3 | LLM arbitration | 1 LLM call | Resolve remaining unmatched subs in a single batch |

**Fix #2b — Secondary dedup pass:**
Even with the overlap extraction guard in the signature, LLMs don't always comply perfectly. The resolver adds a second dedup pass at a lower threshold (0.85) comparing `policy_statement` strings. This catches cases where the same policy is extracted with slightly different wording from overlapping chunks.

**Fix #3 — Three-field matching:**
The original plan matched `sub.parent_policy_name` only against `parent.policy_statement`. But in real documents, the parent_policy_name a sub references (e.g., "Building Energy Efficiency Program") often matches the parent's `section_header` or its own `parent_policy_name` field, not its `policy_statement` (which is a concise summary). The resolver now matches against all three fields and takes the best fuzzy score.

**Fix #9 — Structured statistics:**
Instead of returning a bare `tuple[list, int]`, the resolver returns a `ResolverResult` containing:
- Cleaned policy list
- `ResolverStats` with: input_count, dedup_removed, statement_dedup_removed, deterministic_links, llm_arbitrated, orphan_count, output_count

This gives free observability into resolver behavior per document without extra logging code.

---

### `notebooks/utils/pipeline.py` — New File

**Components:**
- `ChunkResult` dataclass (Fix #8)
- `PipelineResult` dataclass (Fixes #4, #8, #9)
- `ExtractionPipeline` class (Fixes #1, #5)
- `ExtractionPipeline.export_chunk_trace()` static method (Fix #8)

**Fix #1 — Hierarchical carry-forward summary:**
The original plan's `_build_summary` only tracked parent policies (~15 tokens each). Sub-policies and individuals were invisible to later chunks, meaning:
- Subs in chunk 3 couldn't see sibling subs from chunk 1
- Cross-chunk parent-sub linking was blind to individuals

The new summary includes all policy types in a hierarchical format:

```
P1: Electrify 100% of municipal fleet by 2030 — Transportation
  S1.1: Convert all buses to electric by 2028
  S1.2: Add 200 miles of bike lanes
I1: Achieve 90% landfill diversion by 2035 — Waste Management
```

~25 tokens per entry. Still a pure string template — no LLM call.

**Fix #4 — Chunk provenance:**
`PipelineResult.chunk_provenance` is a `dict[str, int]` mapping `policy_statement → chunk_index`. This lets downstream trace tools answer "which part of the document did this policy come from?" without modifying the `ExtractedPolicy` Pydantic schema (which flows through clustering, validation, classification, and export).

**Fix #5 — No single-chunk fast path:**
The original plan had:
```python
if len(chunks) == 1:
    return self.extractor(...)  # skips resolver entirely
```
This meant single-chunk documents got no dedup, no linking cleanup, no orphan logging. Now all documents pass through the resolver for consistency. The resolver is a no-op for clean single-chunk extractions anyway.

**Fix #8 — ChunkResult + chunk_trace.json:**
Each chunk extraction records: word_count, has_overlap, ancestor_headings, policies_extracted, elapsed_seconds, carry_forward_length, and any error. `export_chunk_trace()` writes a `chunk_trace.json` per city containing all chunk metrics plus resolver stats and the provenance mapping.

---

### `notebooks/utils/clustering.py` — Modified (Post-Critique)

**What changed:**
- Added `_normalize_header()` function that lowercases, collapses whitespace, strips trailing punctuation
- `cluster_policies()` now uses `_normalize_header()` instead of bare `.strip()`

**Why (Critique Fix — Risk 6):**
Headers extracted by the LLM varied in formatting, whitespace, or trailing punctuation across chunks. `"5.2 Renewable Energy Targets"` and `"5.2 renewable energy targets "` ended up in different clusters, breaking parent-sub hierarchy. The normalization function prevents these spurious splits.

---

### `notebooks/utils/exports.py` — Modified (Post-Critique)

**What changed:**
- Fixed `build_combined_policies_table()` to read cluster keys matching `clustering.py` output:
  - Individual clusters: reads `cluster.get("individual")` (not `"policy"`)
  - Orphan sub clusters: reads `cluster.get("subs")[0]` (not `"policy"`)

**Why (Critique Fix — Concern 5):**
This was a data-loss bug. `cluster_policies()` stores individuals under the key `"individual"` and orphan subs under `"subs"`, but `build_combined_policies_table()` was looking for `"policy"`. All individual and orphan_sub rows were silently dropped from the combined output.

---

### `notebooks/utils/dspy_resolve.py` — Modified (Post-Critique)

**What changed:**
- LLM arbitration now retries up to 2 attempts with 1-second backoff
- Per-entry defensive parsing: each mapping entry is wrapped in its own try/except
- Handles LLM returning `"null"` as a string instead of JSON null
- Validates indices before casting

**Why (Critique Fix — Risk 4):**
The original code had a bare `try/except` around the entire arbitration. Any single malformed entry (e.g., `"null"` string, invalid index) would abort the whole batch, leaving all unmatched subs orphaned. Now individual entry failures are logged and skipped while valid entries proceed.

---

### `notebooks/utils/consistent_classification.py` — Modified (Post-Critique)

**What changed:**
- Populated `LOCATION_VULNERABILITIES` dict with climate hazard data for all 10 cities
- Added `canonicalize_mechanisms()` method to `ConsistentPolicyClassifier`
- Mechanism fuzzy-clustering at threshold 0.85 before Stage 2

**Why (Critique Fixes — Concern 3, Risk 5):**
1. The `LOCATION_VULNERABILITIES` dict was empty, so Stage 3 enrichment's location-based Adaptation secondary pathway never fired. Now populated with sourced hazard data for Chicago, Seattle, Las Vegas, Miami-Dade, Austin, Dakar, Kuwait, Portugal, Geneva, and Hiroshima.
2. The consistency guarantee depended on the LLM producing identical `canonical_mechanism` strings. Without normalization, variants like `fleet_electrification → transport_emissions_reduction` vs `fleet_electrification → transport_emission_reduction` would get classified independently. The new `canonicalize_mechanisms()` method clusters near-identical mechanism strings using greedy single-linkage fuzzy matching before Stage 2.

---

## Integration Point

The notebook's Step 2 cell changes from:

```python
policies = policy_extractor(document_text=markdowns[key], document_metadata=meta)
```

To:

```python
from utils.pipeline import ExtractionPipeline
from utils.chunking import DocumentChunker
from utils.dspy_resolve import PolicyResolver
from utils.schemas import make_doc_id

WORDS_PER_CHUNK = 6000

# Assign doc_id
for entry in BATCH:
    key = city_key(entry["metadata"])
    entry["metadata"].doc_id = make_doc_id(entry["metadata"], markdowns[key])

# Build pipeline
pipeline = ExtractionPipeline(
    extractor=PolicyExtractor(),
    chunker=DocumentChunker(words_per_chunk=WORDS_PER_CHUNK),
    resolver=PolicyResolver(),
)

# Run per city
result = pipeline.run(document_text=markdowns[key], document_metadata=entry["metadata"])
policies = result.policies

# Optional: write chunk trace
ExtractionPipeline.export_chunk_trace(result, output_dir=f"outputs/{key}")
```

Steps 3-8 (clustering, validation, classification, export) are unchanged.
