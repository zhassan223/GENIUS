# Architecture Critique: Climate Policy Extraction Pipeline

*Independent review of the v4 chunking + extraction + resolution architecture.*

---

## Strengths

**1. Heading-aware chunking with adaptive budget is well-conceived.** The `DocumentChunker` does not blindly split on token count. It scores lines for heading-ness using a weighted feature model, then uses greedy bin-packing that respects section boundaries. The adaptive budget that shrinks as the carry-forward summary grows is a genuinely thoughtful detail -- it acknowledges that the LLM's effective input capacity decreases as more context is injected. The floor at `MIN_CHUNK_WORDS = 800` prevents degenerate tiny chunks.

**2. Three-stage resolution is the right decomposition.** Separating dedup (cheap string similarity), deterministic linking (fuzzy match without LLM), and LLM arbitration (expensive, only for residual cases) is a sound cost-performance tradeoff. Most parent-sub links will be resolved in Stage 2 at zero LLM cost. Stage 3 is batched into a single call.

**3. Mechanism-level classification with registry propagation solves a real consistency problem.** The insight that structurally equivalent policies across cities should get identical labels is correct, and the three-stage classification pipeline (extract mechanism, classify mechanism once, enrich per-instance) is the canonical way to enforce this.

**4. The carry-forward summary is compact and structured.** Using a hierarchical text index rather than re-injecting raw policies keeps token costs manageable. Truncating statements to 100 characters is pragmatic.

**5. Full observability through ChunkResult, ResolverStats, chunk traces, and per-policy JSON traces.** The pipeline produces a detailed audit trail critical for debugging extraction failures across 10+ country documents.

**6. Initiative-level validation is philosophically sound.** The recognition that sub-actions inherit context from parents and should be evaluated leniently as a cluster rather than individually is well-grounded in how real policy documents are structured.

---

## Weaknesses & Risks

### Risk 1: O(n^2) Deduplication Will Not Scale

The `_deduplicate` method in `dspy_resolve.py` uses a nested loop with `SequenceMatcher`. For a document yielding 200+ policies, this means ~20,000 string comparisons on multi-sentence strings.

**Scenario:** A 150-page NDC with dense policy sections yields 300+ extracted policies. Deduplication alone takes 45,000+ comparisons, potentially adding 30-60 seconds.

**Mitigation:** Use MinHash/LSH for approximate dedup as a fast first pass, falling back to `SequenceMatcher` only for candidate pairs.

### Risk 2: Carry-Forward Summary Is Unbounded in Practice

`_build_summary()` grows with every chunk. For a document with 50 parents each with 5 subs, the summary could reach 8,000-10,000 tokens, triggering the "lost in the middle" phenomenon where the LLM ignores entries in the middle of the summary.

**Scenario:** By chunk 15 of a 200-page plan, the summary is 6,000+ tokens. Sub-policies in chunk 16 can't link to parents from chunk 3.

**Mitigation:** Implement a sliding window or hierarchical compression. After N entries, compress older entries into sector-level summaries.

### Risk 3: Heading Detection Is Fragile for Real-World PDFs

The heading scorer relies on Markdown conventions, uppercase detection, and numbered patterns. Real PDFs converted via OCR often produce none of these signals. Arabic/French documents may have headings in mixed-case with diacritics.

**Scenario:** Kuwait's NDC has Arabic headings that appear as normal-cased text. Zero headings detected. The entire document is paragraph-split without structural awareness.

**Mitigation:** Add font-size/position-based heading detection from raw PDF metadata. Add language-aware patterns for non-English documents ("Chapitre", "Article", "Titre").

### Risk 4: LLM Arbitration Has No Retry or Validation

The LLM arbitration wraps everything in a bare `try/except` that silently returns 0 on any failure. No schema validation on the JSON response.

**Scenario:** LLM returns `"null"` as a string instead of JSON null. The entire arbitration fails silently and all unmatched subs remain orphaned.

**Mitigation:** Parse per-entry rather than failing the whole batch. Add 1-2 retries. Validate indices before casting.

### Risk 5: Mechanism Canonicalization Depends on LLM String Consistency

The consistency guarantee depends on the LLM producing identical `canonical_mechanism` strings for equivalent policies. No post-processing normalization exists.

**Scenario:** 10 cities produce 5 variants of "fleet electrification" mechanism string. Instead of 1 classification, you get 5 independent (potentially inconsistent) classifications.

**Mitigation:** Add fuzzy-matching clustering on mechanism strings before Stage 2. Or constrain output to a predefined vocabulary of ~50-100 mechanism templates.

### Risk 6: Section Header Clustering Uses Exact String Match

`clustering.py` clusters by exact `section_header` equality. Headers extracted by the LLM may vary in formatting, whitespace, or completeness across chunks.

**Scenario:** Parent has `section_header = "5.2 Renewable Energy Targets"`, sub has `section_header = "5.2 Renewable Energy Targets "` (trailing space). Different clusters, broken hierarchy.

**Mitigation:** Normalize headers before clustering (strip, lowercase, collapse whitespace). One-line fix.

### Risk 7: No Error Recovery at Document Level

If a chunk fails, the pipeline `continue`s but the carry-forward summary is missing that chunk's policies. Parents from the failed chunk cause cascading orphans.

**Mitigation:** Implement chunk-level retry (at least once). Log prominently when subsequent chunks can't link to expected parents.

---

## Specific Technical Concerns

### Concern 1: `make_doc_id` Uses 6-Character Hash Truncation

24 bits of entropy. Birthday-paradox collision reaches 1% at ~500 documents, 50% at ~5,000.

**Mitigation:** Use at least 12 hex characters (48 bits).

### Concern 2: `_build_summary` Matching Uses Substring Containment

`sub.parent_policy_name.lower() in pstmt.lower()` is asymmetric and produces false matches. "Energy" matches "Renewable Energy", "Building Energy Codes", and "Energy Storage".

**Mitigation:** Use fuzzy matching with threshold instead of substring containment.

### Concern 3: `LOCATION_VULNERABILITIES` Dictionary Is Empty

Stage 3 location enrichment never fires. Miami water policies won't get Adaptation secondaries.

**Mitigation:** Populate from climate risk databases (ND-GAIN, World Bank).

### Concern 4: No Post-Extraction Output Normalization

If the LLM returns `policy_type = "Parent"` instead of `"parent"`, Pydantic may reject it silently or accept malformed data.

**Mitigation:** Add normalization layer: lowercase `policy_type`, validate against allowed set, strip all string fields.

### Concern 5: Cluster Schema Contract Bug Between `clustering.py` and `exports.py`

`cluster_policies()` uses key `"individual"` for individual clusters. `build_combined_policies_table()` looks for `cluster.get("policy")`. Individuals and orphan_subs are silently dropped.

**Mitigation:** Align key names. This is a data loss bug.

### Concern 6: Sequential Processing Is a Throughput Bottleneck

12-chunk document = 12 serial LLM calls. 10 countries x 8-12 chunks = 80-120 serial calls = 13-60 minutes just for extraction.

**Mitigation:** Document-level parallelism + checkpointing.

---

## Alternative Approaches Considered

### 1. Map-Reduce Extraction
Extract from each chunk independently (fully parallel), merge/resolve in reduce phase. Faster but produces more duplicates. Probably better for production. The resolver already handles the reduce phase.

### 2. Hierarchical Summarization + Single-Pass
Generate document outline first, then extract with full outline as context for every chunk. Directly addresses "lost in the middle" and growing-summary budget. Strongest alternative.

### 3. Retrieval-Augmented Extraction
Embed chunks, retrieve semantically similar context. Over-engineered for sequential document extraction.

### 4. Constrained Decoding / Function Calling
Use native structured output modes instead of DSPy ChainOfThought + post-hoc parsing. Eliminates an entire class of silent failures. Strongly recommended as a complement.

---

## Resolution Status

*The following issues from this critique have been addressed:*

| # | Issue | Status | Fix Location |
|---|-------|--------|-------------|
| Concern 5 | Cluster schema contract bug | **FIXED** | `exports.py` — reads `"individual"` and `"subs"[0]` keys matching `clustering.py` output |
| Concern 3 | Empty LOCATION_VULNERABILITIES | **FIXED** | `consistent_classification.py` — populated for all 10 cities with sourced hazard data |
| Risk 5 | Mechanism string canonicalization | **FIXED** | `consistent_classification.py` — `canonicalize_mechanisms()` fuzzy-clusters variants (threshold 0.85) before Stage 2 |
| Risk 6 | Section header exact match | **FIXED** | `clustering.py` — `_normalize_header()` lowercases, collapses whitespace, strips trailing punctuation |
| Risk 7 | No chunk-level retry | **FIXED** | `pipeline.py` — exponential backoff retry in `run()` (configurable `max_chunk_retries`) |
| Concern 4 | No post-extraction normalization | **FIXED** | `pipeline.py` — `_normalize_policies()` lowercases policy_type, strips whitespace, rejects empty fields |
| Concern 2 | `_build_summary` substring matching | **FIXED** | `pipeline.py` — uses `_similarity()` fuzzy matching (threshold 0.80) instead of `in` operator |
| Risk 4 | LLM arbitration no retry/validation | **FIXED** | `dspy_resolve.py` — 2-attempt retry, per-entry defensive parsing, handles "null" string, validates indices |

*Second-round fixes (from independent re-critique):*

| # | Issue | Status | Fix Location |
|---|-------|--------|-------------|
| N3+N4 | Clustering ignores resolver linkage / parent overwrite | **FIXED** | `clustering.py` — rewritten to group subs by `parent_policy_name` match to parent `policy_statement`, not by `section_header` |
| N1 | Initiative validator import shim fragile | **FIXED** | `utils/initiative_validator.py` — uses `importlib` + explicit `sys.path` resolution |
| N2 | Mechanism normalization regex ambiguity + registry key mismatch | **FIXED** | `consistent_classification.py` — explicit arrow alternation `(?:→\|->)`, stores `_mechanism_key` for registry lookups |
| N5 | Orphan count undercounts dangling refs | **FIXED** | `dspy_resolve.py` — counts subs whose `parent_policy_name` doesn't match any actual parent |
| N7 | Adaptive budget dead code | **FIXED** | `chunking.py` — removed `accumulated_policy_count` param and unused constants; budget is now a simple property |
| N10-12 | Inline imports in hot paths | **FIXED** | `dspy_resolve.py` — `re`, `json`, `time` moved to module-level imports |
| (gap) | Vulnerability lookup key normalization | **FIXED** | `consistent_classification.py` — `build_vulnerability_context()` normalizes hyphens/spaces/whitespace |

*Third-round fixes:*

| # | Issue | Status | Fix Location |
|---|-------|--------|-------------|
| (P1) | `cluster_id` always None in cluster dicts | **FIXED** | `clustering.py` — `cluster_policies()` assigns sequential `cluster_id` to every cluster |
| (P1) | Registry keyed by raw string, fragile to serialization | **FIXED** | `consistent_classification.py` — `stage2_classify_mechanisms()` groups by `_mechanism_key` (normalized), not raw `canonical_mechanism` |
| (P2) | Vulnerability lookup case-sensitive | **FIXED** | `consistent_classification.py` — `build_vulnerability_context()` tries exact then title-case lookup |

### Remaining items (not yet addressed):

| # | Issue | Status | Notes |
|---|-------|--------|-------|
| Risk 1 | O(n^2) dedup scaling | Deferred | Not a bottleneck at current scale (200-300 policies). MinHash/LSH recommended if scaling to 1000+ |
| Risk 2 | Carry-forward summary unbounded | Deferred | Budget is fixed; set `words_per_chunk` conservatively. Sliding window recommended for 200+ page documents |
| Risk 3 | Heading detection fragile for OCR | Deferred | Mitigated by paragraph fallback. Font-metadata detection recommended for non-English PDFs |
| Concern 1 | 6-char hash truncation | Accepted risk | 1% collision at ~500 docs. Acceptable for current 10-city scope |
| Concern 6 | Sequential processing bottleneck | Deferred | Document-level parallelism + checkpointing recommended for production throughput |

---

## Verdict

**This architecture has moved from research prototype to hardened pipeline.** The eight critical bugs and gaps identified in the original critique have been resolved. The remaining items are scaling concerns that don't affect correctness at the current 10-city scope.

### What this architecture gets fundamentally right:
- Separation of concerns with well-defined interfaces
- Mechanism registry for cross-city consistency
- Observability infrastructure (traces, stats, provenance)
- Domain-appropriate validation semantics (strict individuals, lenient initiatives)
- Defensive validation at component seams (post-extraction normalization, header normalization)
- Retry logic at all LLM call sites (chunk extraction, LLM arbitration)

### What would take this to production at scale:
- MinHash/LSH for O(n log n) dedup on 1000+ policy corpora
- Sliding window or hierarchical compression for carry-forward summary
- Document-level parallelism + checkpointing for throughput
- Font-metadata heading detection for OCR'd / non-English PDFs
