# GENIUS Pipeline — Data Flow (`dspy_pipeline_v4.ipynb`)

## Overview

The pipeline ingests climate policy documents (PDF or pre-converted Markdown) for up to 10 cities, extracts and validates individual policies and multi-policy initiatives, then classifies every valid policy using a three-stage mechanism registry. All LLM calls are powered by **DSPy** with GPT-5.2; heavy steps are parallelized via `ParallelExecutor` (`NUM_THREADS = 8`).

---

## Pipeline Diagram

```mermaid
flowchart TD
    %% ── INPUT ────────────────────────────────────────────────────────────────
    subgraph INPUT["📥 Inputs"]
        PDF["PDF files\n(pdfs/)"]
        MD["Pre-converted Markdown\n(docs/cities/*.md)"]
        BATCH["BATCH config\n(DocumentMetadata × 10 cities)"]
    end

    %% ── STEP 1 ───────────────────────────────────────────────────────────────
    subgraph S1["Step 1 — Load Markdown"]
        direction LR
        S1A["Priority 1: markdown_path\nfrom BATCH config"]
        S1B["Priority 2: cached\noutputs/{key}_markdown.md"]
        S1C["Priority 3: Docling\nPDF → Markdown (live)"]
    end
    markdowns[["markdowns\ncity_key → str"]]

    %% ── STEP 2 ───────────────────────────────────────────────────────────────
    subgraph S2["Step 2 — Extract Policies (parallelized)"]
        S2LLM["PolicyExtractor\n(DSPy + GPT-5.2)"]
    end
    all_extracted[["all_extracted\ncity_key → List[ExtractedPolicy]"]]

    %% ── STEP 3 ───────────────────────────────────────────────────────────────
    subgraph S3["Step 3 — Cluster Policies"]
        S3A["cluster_policies()\ngroups by section header"]
        S3B["Types:\n• parent_with_subs\n• individual\n• orphan_sub"]
    end
    all_clusters[["all_clusters\ncity_key → List[dict]"]]

    %% ── STEP 4 ───────────────────────────────────────────────────────────────
    subgraph S4["Step 4 — Build Policy Records"]
        S4A["clusters_to_records()\nflattens clusters → rows"]
    end
    all_df_policies[["all_df_policies\ncity_key → DataFrame\n(cluster_id, role, sector, …)"]]

    %% ── STEP 5 ───────────────────────────────────────────────────────────────
    subgraph S5["Step 5 — Validate Individuals (parallelized)"]
        S5A["Filter: role == 'individual'"]
        S5B["PolicyValidator\n(DSPy + GPT-5.2)"]
        S5C["Flatten ValidationMetrics\ninto columns"]
    end
    all_df_final[["all_df_final\ncity_key → DataFrame\n(+ validation_results columns)"]]

    %% ── STEP 6 ───────────────────────────────────────────────────────────────
    subgraph S6["Step 6 — Validate Initiatives (parallelized)"]
        S6A["Filter: cluster_type == 'parent_with_subs'"]
        S6B["InitiativeValidator\n(DSPy + GPT-5.2)"]
        S6C["Metrics: coverage, coherence,\ninitiative_result, final_verdict"]
    end
    all_df_initiatives[["all_df_initiatives\ncity_key → DataFrame\n(initiative-level verdicts)"]]

    %% ── STEP 7 ───────────────────────────────────────────────────────────────
    subgraph S7["Step 7 — Export Combined Results"]
        S7A["build_combined_policies_table()\n(individuals + initiative rows)"]
        S7B["filter_valid_policies()\nfinal_verdict == True only"]
        S7C["export_combined_table_and_traces()"]
    end
    all_combined[["all_combined\ncity_key → DataFrame\n(all policies)"]]
    all_valid[["all_valid\ncity_key → DataFrame\n(valid policies only)"]]

    %% ── STEP 8 ───────────────────────────────────────────────────────────────
    subgraph S8["Step 8 — 3-Stage Registry Classification"]
        subgraph STG1["Stage 1 — Mechanism Extraction (parallelized)"]
            S8S1["ConsistentPolicyClassifier\nextract_mechanism()\n→ canonical_mechanism, sector"]
        end
        subgraph STG2["Stage 2 — Mechanism Classification (sequential)"]
            S8S2["stage2_classify_mechanisms()\nDeduplicate mechanisms\n→ mechanism_registry\n(1 LLM call per unique mechanism)"]
        end
        subgraph STG3["Stage 3 — Policy Enrichment (parallelized)"]
            S8S3["enrich_policy()\nLocked: primary/secondary from Stage 2\nAdded: instrument_type, climate_relevance,\nkey_indicators, co_benefits"]
        end
    end
    all_classified[["all_classified\ncity_key → DataFrame\n(full classification metadata)"]]

    %% ── OUTPUTS ──────────────────────────────────────────────────────────────
    subgraph OUTPUTS["📤 Outputs — outputs/{City}/"]
        O1["combined_policies.csv\n(valid individual + initiative rows)"]
        O2["trace_individual_policies.csv\n(all individual rows + validation detail)"]
        O3["trace_initiative_policies.csv\n(all initiative rows + validation detail)"]
        O4["classified_policies.csv\n(valid policies + classification fields)"]
        O5["trace_classification.json\n(mechanism + enrichment trace)"]
    end

    %% ── FLOW CONNECTIONS ─────────────────────────────────────────────────────
    PDF & MD --> S1
    BATCH --> S1
    S1 --> markdowns

    markdowns --> S2LLM --> all_extracted
    all_extracted --> S3A --> S3B --> all_clusters
    all_clusters --> S4A --> all_df_policies

    all_df_policies --> S5A --> S5B --> S5C --> all_df_final
    all_clusters --> S6A --> S6B --> S6C --> all_df_initiatives

    all_df_policies & all_df_final & all_df_initiatives & all_clusters --> S7A --> all_combined
    all_combined & all_df_final & all_df_initiatives --> S7B --> all_valid
    all_combined & all_df_initiatives & all_df_final --> S7C

    S7C --> O1 & O2 & O3
    all_valid --> S8

    S8S1 --> S8S2 --> S8S3
    S8 --> all_classified
    all_classified --> O4 & O5
```

---

## Step-by-Step Data Flow

### Setup

| Object | Type | Description |
|---|---|---|
| `BATCH` | `list[dict]` | 10 city entries — each has `DocumentMetadata`, `pdf_path`, `markdown_path` |
| `lm` | `dspy.LM` | GPT-5.2 language model, shared by all DSPy modules |
| `NUM_THREADS` | `int` | `8` — parallelism cap for all LLM steps |

---

### Step 1 — Load Markdown → `markdowns`

**Input:** `BATCH` entries + file system  
**Output:** `markdowns: dict[city_key → str]`

Loads each city's document text with a 3-priority fallback:
1. `markdown_path` declared in `BATCH` (pre-converted file, e.g. `docs/cities/chicago.md`)
2. Cached file from a previous run at `outputs/{key}_markdown.md`
3. Live Docling PDF conversion (saves result to cache)

---

### Step 2 — Extract Policies → `all_extracted`

**Input:** `markdowns`  
**Output:** `all_extracted: dict[city_key → List[ExtractedPolicy]]`  
**LLM:** `PolicyExtractor` (parallelized, 1 call/city)

Sends the full Markdown document to the LLM. Each `ExtractedPolicy` contains:
- `policy_statement`, `verbatim_text`
- `policy_type` (`parent` / `sub` / `individual`)
- `parent_policy_name`, `section_header`, `sector`, `extraction_rationale`

Also writes `outputs/{key}_extracted_policies.json`.

---

### Step 3 — Cluster Policies → `all_clusters`

**Input:** `all_extracted`  
**Output:** `all_clusters: dict[city_key → List[dict]]`  
**No LLM** — deterministic grouping

Groups policies by section header into three cluster types:
- `parent_with_subs` — a parent policy with its sub-actions
- `individual` — standalone policies
- `orphan_sub` — sub-policies whose parent wasn't found

Also writes `outputs/{key}_policy_clusters.json`.

---

### Step 4 — Build Policy Records → `all_df_policies`

**Input:** `all_clusters`  
**Output:** `all_df_policies: dict[city_key → pd.DataFrame]`  
**No LLM** — structural flattening

Calls `clusters_to_records()` to flatten nested clusters into a uniform row-per-policy DataFrame. Standard columns: `cluster_id`, `cluster_type`, `role`, `section_header`, `sector`, `policy_statement`, `parent_statement`, `verbatim_text`, `extraction_rationale`.

---

### Step 5 — Validate Individual Policies → `all_df_final`

**Input:** `all_policy_records` (rows where `role == "individual"`)  
**Output:** `all_df_final: dict[city_key → pd.DataFrame]`  
**LLM:** `PolicyValidator` (parallelized, 1 call/policy)

Each policy is validated against a `PolicyValidationSignature`. `ValidationMetrics` is flattened into columns covering specificity, measurability, binding mechanism, spatial scope, and `final_verdict`.

---

### Step 6 — Validate Initiatives → `all_df_initiatives`

**Input:** `all_clusters` (`parent_with_subs` entries)  
**Output:** `all_df_initiatives: dict[city_key → pd.DataFrame]`  
**LLM:** `InitiativeValidator` (parallelized, 1 call/initiative)

Each parent cluster is assessed as a whole initiative via `build_initiative_context()` + `InitiativeValidator`. Output fields include:
- `coverage_score`, `coherence_score`
- `initiative_result` (`SOUND` / `PARTIAL` / `WEAK`)
- `final_verdict`, `confidence_score`
- Per-sub `sub_assessments` with individual `strength` ratings

---

### Step 7 — Export Combined Results → `all_combined`, `all_valid`

**Input:** `all_df_policies`, `all_df_final`, `all_df_initiatives`, `all_clusters`  
**Output:**  
- `all_combined` — every policy row (all verdicts)  
- `all_valid` — `final_verdict == True` rows only

**Written files under `outputs/{City}/`:**

| File | Contents |
|---|---|
| `combined_policies.csv` | Valid policies (individual + initiative clusters) |
| `trace_individual_policies.csv` | All individual rows + full validation detail |
| `trace_individual_policies_valid.csv` | Valid individual rows only |
| `trace_initiative_policies.csv` | All initiative rows + full validation detail |
| `trace_initiative_policies_valid.csv` | Valid initiative rows only |

---

### Step 8 — 3-Stage Registry Classification → `all_classified`

**Input:** `all_valid` (pooled across all cities)  
**Output:** `all_classified: dict[city_key → pd.DataFrame]`

#### Stage 1 — Mechanism Extraction (parallelized)
One LLM call per policy. Extracts a canonical `<action> → <climate_effect>` string (`canonical_mechanism`), normalised sector, and `mechanism_description`.

#### Stage 2 — Mechanism Classification (sequential, deduplicated)
One LLM call per **unique** canonical mechanism. Builds `mechanism_registry` so identical mechanisms always receive identical labels across all cities. Fields locked here:
`primary_category`, `secondary_categories`, `primary_causal_pathway`, `causal_mechanism_detail`, `dominant_pathway_test`, `classification_reasoning`, `confidence_score`

**LLM call savings:** `(total policies) − (unique mechanisms)` calls avoided vs. row-by-row approach.

#### Stage 3 — Policy Enrichment (parallelized)
One LLM call per policy. Stage 2 labels are **locked** (cannot be changed). Stage 3 only adds instance-specific fields:
`instrument_type`, `instrument_directness`, `climate_relevance`, `additional_secondary`, `key_indicators`, `co_benefits`

**Written files under `outputs/{City}/`:**

| File | Contents |
|---|---|
| `classified_policies.csv` | Full classification metadata per valid policy |
| `trace_classification.json` | Mechanism + enrichment trace for audit |

---

## In-Memory State Summary

| Variable | Populated After | Content |
|---|---|---|
| `markdowns` | Step 1 | Raw document text per city |
| `all_extracted` | Step 2 | Raw `ExtractedPolicy` objects |
| `all_clusters` | Step 3 | Grouped policy clusters |
| `all_policy_records` / `all_df_policies` | Step 4 | Flat DataFrame of all extracted rows |
| `all_df_final` | Step 5 | Individual validation results |
| `all_df_initiatives` | Step 6 | Initiative validation results |
| `all_combined` / `all_valid` | Step 7 | Final merged + filtered tables |
| `all_classified` | Step 8 | Classification-enriched valid policies |

---

## Cities Processed

| Key | Country | State / Province |
|---|---|---|
| `Chicago` | United States | Illinois |
| `Seattle` | United States | Washington |
| `Las_Vegas` | United States | Nevada |
| `Miami_Dade` | United States | Florida |
| `Austin` | United States | Texas |
| `Dakar` | Senegal | — |
| `Kuwait` | Kuwait | — |
| `Portugal` | Portugal | — |
| `Geneva` | Switzerland | — |
| `Hiroshima` | Japan | — |
