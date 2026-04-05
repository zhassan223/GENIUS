from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Minimal shared schema for the combined table
# All roles (individual / parent / sub) share exactly these columns.
# ---------------------------------------------------------------------------
_COMBINED_COLS = [
    "cluster_id",
    "cluster_type",
    "role",
    "sector",
    "section_header",
    "policy_statement",
    "parent_statement",   # populated for subs; None for others
    "parent_row_type",    # populated for parent/sub rows when initiative validation exists
    "verbatim_text",
    "extraction_rationale",
]

_FINAL_COMBINED_COLS = [
    "policy_id",
    "role",
    "parent_statement",
    "parent_row_type",
    "policy_statement",
    "primary_category",
    "secondary_categories",
    "typology_code",
    "trace_path",
]

_TRACE_POLICY_COLS = [
    "policy_statement",
    "role",
    "sector",
    "canonical_mechanism",
    "mechanism_description",
    "primary_category",
    "secondary_categories",
    "typology_code",
    "typology_confidence",
    "typology_evidence_quote",
    "primary_causal_pathway",
    "causal_mechanism_detail",
    "dominant_pathway_test",
    "mechanism_classification_reasoning",
    "mechanism_confidence",
    "mechanism_edge_case_notes",
    "instrument_type",
    "instrument_directness",
    "climate_relevance",
    "key_indicators",
    "co_benefits",
    "instance_edge_case_notes",
    "classification_schema_version",
    "secondary_profile",
]

_TRACE_LOOKUP_COLS = [
    "policy_id",
    "role",
    "parent_statement",
    "policy_statement",
    "primary_category",
    "typology_code",
    "trace_path",
    "validation_trace_csv",
    "validation_lookup_column",
    "validation_lookup_value",
]


def _safe_filename(s: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", str(s)).strip()
    s = re.sub(r"[\s-]+", "_", s)
    return s[:maxlen]


def _initiative_parent_row_type_map(df_initiatives: Optional[pd.DataFrame]) -> dict[str, str]:
    if (
        df_initiatives is None
        or df_initiatives.empty
        or "parent_statement" not in df_initiatives.columns
        or "parent_row_type" not in df_initiatives.columns
    ):
        return {}

    initiative_parent_types = (
        df_initiatives.loc[:, ["parent_statement", "parent_row_type"]]
        .dropna(subset=["parent_statement", "parent_row_type"])
        .drop_duplicates(subset=["parent_statement"], keep="first")
    )
    return dict(
        zip(
            initiative_parent_types["parent_statement"],
            initiative_parent_types["parent_row_type"],
        )
    )


def _build_trace_records(df_classified: pd.DataFrame) -> pd.DataFrame:
    """
    Build a stable per-policy trace manifest from classified policies.

    The `policy_id` is derived from row order in `classified_policies.csv` so the
    generated JSON filenames remain stable across the trace manifest and the
    written `policy_traces/` directory.
    """
    if df_classified.empty:
        return pd.DataFrame(columns=["policy_id", "policy_statement", "role", "primary_category", "secondary_categories", "typology_code", "trace_path"])

    df_traces = df_classified.copy().reset_index(drop=True)
    df_traces["policy_id"] = df_traces.index.map(lambda idx: f"{idx:03d}")
    df_traces["role"] = df_traces.get("role", pd.Series([None] * len(df_traces))).fillna("individual")
    df_traces["trace_path"] = df_traces.apply(
        lambda row: (
            f"policy_traces/{row['policy_id']}_"
            f"{_safe_filename(row.get('policy_statement', f'policy_{row.name}'))}.json"
        ),
        axis=1,
    )

    keep_cols = [c for c in ["policy_id", "policy_statement", "role", "primary_category", "secondary_categories", "typology_code", "trace_path"] if c in df_traces.columns]
    return df_traces[keep_cols].drop_duplicates(subset=["policy_statement", "role"]).reset_index(drop=True)


def export_final_combined_with_traces(
    *,
    combined: pd.DataFrame,
    df_classified: pd.DataFrame,
    output_dir: str | Path,
) -> dict:
    """
    Write a presentation-friendly combined CSV plus trace artifacts.

    Outputs:
      - combined_policies.csv      <- minimal final table
      - policy_trace_lookup.csv    <- how to locate each row's validator trace
      - policy_traces/*.json       <- one classification trace per policy row
      - excluded_policies_trace.csv <- rows removed by deterministic climate screen
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written: dict = {}
    if "climate_screen" in df_classified.columns:
        keep_mask = df_classified["climate_screen"].fillna("exclude") != "exclude"
        df_kept = df_classified.loc[keep_mask].copy()
        df_excluded = df_classified.loc[~keep_mask].copy()
    else:
        df_kept = df_classified.copy()
        df_excluded = pd.DataFrame(columns=df_classified.columns)

    excluded_trace_path = output_dir / "excluded_policies_trace.csv"
    df_excluded.to_csv(excluded_trace_path, index=False)
    written["excluded_policies_trace"] = str(excluded_trace_path)

    trace_manifest = _build_trace_records(df_kept)
    traces_dir = output_dir / "policy_traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    for old_trace in traces_dir.glob("*.json"):
        old_trace.unlink()

    trace_cols = [c for c in _TRACE_POLICY_COLS if c in df_kept.columns]
    for idx, row in df_kept.reset_index(drop=True).iterrows():
        trace = {c: (None if pd.isna(row[c]) else row[c]) for c in trace_cols}
        stmt_slug = _safe_filename(row.get("policy_statement", f"policy_{idx}"))
        fname = f"{idx:03d}_{stmt_slug}.json"
        with open(traces_dir / fname, "w", encoding="utf-8") as f:
            json.dump(trace, f, ensure_ascii=False, indent=2)

    written["policy_traces_dir"] = str(traces_dir)

    if combined.empty or trace_manifest.empty:
        final_df = pd.DataFrame(columns=_FINAL_COMBINED_COLS)
    else:
        merge_cols = [c for c in ["policy_statement", "role"] if c in combined.columns and c in trace_manifest.columns]
        if not merge_cols:
            raise ValueError("combined and classified outputs must share policy_statement and role columns.")

        final_df = combined.merge(trace_manifest, on=merge_cols, how="inner", suffixes=("", "_trace"))
        if "policy_id_trace" in final_df.columns:
            final_df["policy_id"] = final_df["policy_id_trace"]
        if "primary_category" not in final_df.columns and "primary_category_trace" in final_df.columns:
            final_df["primary_category"] = final_df["primary_category_trace"]
        if "secondary_categories" not in final_df.columns and "secondary_categories_trace" in final_df.columns:
            final_df["secondary_categories"] = final_df["secondary_categories_trace"]
        if "typology_code" not in final_df.columns and "typology_code_trace" in final_df.columns:
            final_df["typology_code"] = final_df["typology_code_trace"]
        if "trace_path" not in final_df.columns and "trace_path_trace" in final_df.columns:
            final_df["trace_path"] = final_df["trace_path_trace"]
        final_df = final_df.reindex(columns=_FINAL_COMBINED_COLS)

    combined_path = output_dir / "combined_policies.csv"
    final_df.to_csv(combined_path, index=False)
    written["combined_policies"] = str(combined_path)

    if final_df.empty:
        trace_lookup = pd.DataFrame(columns=_TRACE_LOOKUP_COLS)
    else:
        trace_lookup = final_df.copy()
        trace_lookup["validation_trace_csv"] = trace_lookup["role"].map(
            lambda role: "trace_individual_policies_valid.csv" if role == "individual" else "trace_initiative_policies_valid.csv"
        )
        trace_lookup["validation_lookup_column"] = trace_lookup["role"].map(
            lambda role: "policy_statement" if role == "individual" else "parent_statement"
        )
        trace_lookup["validation_lookup_value"] = trace_lookup.apply(
            lambda row: (
                row["policy_statement"]
                if row["role"] in ("individual", "parent")
                else row["parent_statement"]
            ),
            axis=1,
        )
        trace_lookup = trace_lookup.reindex(columns=_TRACE_LOOKUP_COLS)

    trace_lookup_path = output_dir / "policy_trace_lookup.csv"
    trace_lookup.to_csv(trace_lookup_path, index=False)
    written["policy_trace_lookup"] = str(trace_lookup_path)

    return written


def build_combined_policies_table(
    *,
    df_policies: pd.DataFrame,
    df_final_individual: Optional[pd.DataFrame] = None,
    df_initiatives: Optional[pd.DataFrame] = None,
    policy_clusters: Optional[list] = None,
) -> pd.DataFrame:
    """
    Build a minimal combined table from policy_clusters + df_final_individual.

    Every row (individual / parent / sub) gets the same minimal columns.
    All validation detail lives in the separate trace files.

    Fixes applied:
    - orphan_sub clusters are now included as individual rows (were silently dropped).
    - Any validated individual policy in df_final_individual that is missing from
      the clusters is backfilled so no validated policy is lost from the output.
    """
    if policy_clusters is None:
        raise ValueError("policy_clusters is required to build the combined table.")

    rows: List[dict] = []
    # Track statements added from clusters so we can backfill the rest
    clustered_stmts: set = set()
    parent_row_type_by_statement = _initiative_parent_row_type_map(df_initiatives)

    for cluster in policy_clusters:
        ctype = cluster.get("cluster_type", "")
        cid   = cluster.get("cluster_id")

        if ctype in ("individual", "orphan_sub"):
            # Fix: cluster_policies() uses "individual" key for individuals
            # and "subs"[0] for orphan_subs — align with actual schema
            if ctype == "individual":
                p = cluster.get("individual") or cluster.get("policy")
            else:  # orphan_sub
                subs_list = cluster.get("subs") or []
                p = subs_list[0] if subs_list else cluster.get("policy")
            if p is None:
                continue
            d = p.model_dump() if hasattr(p, "model_dump") else dict(p)
            stmt = d.get("policy_statement")
            clustered_stmts.add(stmt)
            rows.append({
                "cluster_id":           cid,
                "cluster_type":         ctype,
                "role":                 "individual",
                "sector":               d.get("sector"),
                "section_header":       d.get("section_header"),
                "policy_statement":     stmt,
                "parent_statement":     None,
                "parent_row_type":      None,
                "verbatim_text":        d.get("verbatim_text"),
                "extraction_rationale": d.get("extraction_rationale"),
            })

        elif ctype == "parent_with_subs":
            parent = cluster.get("parent")
            subs   = cluster.get("subs") or []
            if parent is None:
                continue
            pd_ = parent.model_dump() if hasattr(parent, "model_dump") else dict(parent)
            parent_stmt = pd_.get("policy_statement")
            parent_row_type = parent_row_type_by_statement.get(parent_stmt)

            rows.append({
                "cluster_id":           cid,
                "cluster_type":         ctype,
                "role":                 "parent",
                "sector":               pd_.get("sector"),
                "section_header":       pd_.get("section_header"),
                "policy_statement":     parent_stmt,
                "parent_statement":     None,
                "parent_row_type":      parent_row_type,
                "verbatim_text":        pd_.get("verbatim_text"),
                "extraction_rationale": pd_.get("extraction_rationale"),
            })

            for sub in subs:
                sd = sub.model_dump() if hasattr(sub, "model_dump") else dict(sub)
                rows.append({
                    "cluster_id":           cid,
                    "cluster_type":         ctype,
                    "role":                 "sub",
                    "sector":               sd.get("sector") or pd_.get("sector"),
                    "section_header":       sd.get("section_header") or pd_.get("section_header"),
                    "policy_statement":     sd.get("policy_statement"),
                    "parent_statement":     parent_stmt,
                    "parent_row_type":      parent_row_type,
                    "verbatim_text":        sd.get("verbatim_text"),
                    "extraction_rationale": sd.get("extraction_rationale"),
                })

    # ── Backfill: validated individual policies missing from clusters ────────
    # Step 5 validates all individual records; if any weren't picked up by the
    # cluster walk above (e.g. cluster_type mismatch or missing policy key),
    # add them now so they aren't silently dropped from combined / valid outputs.
    n_backfilled = 0
    if df_final_individual is not None and not df_final_individual.empty:
        for _, row in df_final_individual.iterrows():
            stmt = row.get("policy_statement")
            if stmt in clustered_stmts:
                continue
            n_backfilled += 1
            rows.append({
                "cluster_id":           row.get("cluster_id"),
                "cluster_type":         row.get("cluster_type", "individual"),
                "role":                 "individual",
                "sector":               row.get("sector"),
                "section_header":       row.get("section_header"),
                "policy_statement":     stmt,
                "parent_statement":     None,
                "parent_row_type":      None,
                "verbatim_text":        row.get("verbatim_text"),
                "extraction_rationale": row.get("extraction_rationale"),
            })

    if n_backfilled:
        print(f"  [build_combined] backfilled {n_backfilled} individual policies missing from clusters.")

    return pd.DataFrame(rows, columns=_COMBINED_COLS)


def filter_valid_policies(
    combined: pd.DataFrame,
    df_final_individual: Optional[pd.DataFrame],
    df_initiatives: Optional[pd.DataFrame],
) -> pd.DataFrame:
    """
    Return rows from `combined` where the underlying policy passed validation
    (final_verdict == True).

    - Individual rows  → matched via policy_statement against df_final_individual
    - Parent rows      → matched via policy_statement against df_initiatives.parent_statement
    - Sub rows         → matched via parent_statement against df_initiatives.parent_statement
                         (inherit the parent initiative's verdict)
    """
    if combined.empty:
        return combined.copy()

    valid_mask = pd.Series(False, index=combined.index)

    # Individual policies
    if df_final_individual is not None and not df_final_individual.empty:
        if "final_verdict" in df_final_individual.columns and "policy_statement" in df_final_individual.columns:
            valid_stmts = set(
                df_final_individual.loc[
                    df_final_individual["final_verdict"] == True, "policy_statement"
                ].dropna()
            )
            valid_mask |= (
                (combined["role"] == "individual")
                & (combined["policy_statement"].isin(valid_stmts))
            )

    # Parent + sub rows (initiative verdict)
    if df_initiatives is not None and not df_initiatives.empty:
        if "final_verdict" in df_initiatives.columns and "parent_statement" in df_initiatives.columns:
            valid_parents = set(
                df_initiatives.loc[
                    df_initiatives["final_verdict"] == True, "parent_statement"
                ].dropna()
            )
            valid_mask |= (
                (combined["role"] == "parent")
                & (combined["policy_statement"].isin(valid_parents))
            )
            valid_mask |= (
                (combined["role"] == "sub")
                & (combined["parent_statement"].isin(valid_parents))
            )

    return combined[valid_mask].reset_index(drop=True)


def export_combined_table_and_traces(
    *,
    combined: pd.DataFrame,
    df_initiatives: Optional[pd.DataFrame],
    df_final_individual: Optional[pd.DataFrame] = None,
    output_dir: str | Path = "outputs/policy_pipeline_v4",
) -> dict:
    """
    Write:
      - combined_policies.csv                  <- valid policies only (individual + initiative
                                                  clusters), minimal 9-column table
      - trace_individual_policies.csv          <- ALL individual validation details
      - trace_individual_policies_valid.csv    <- only final_verdict == True
      - trace_initiative_policies.csv          <- ALL initiative validation details
      - trace_initiative_policies_valid.csv    <- only final_verdict == True

    Returns a dict mapping label -> path string.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written: dict = {}

    # 1. Combined table — valid policies only (individual + initiative clusters), minimal columns
    df_valid = filter_valid_policies(combined, df_final_individual, df_initiatives)
    combined_path = output_dir / "combined_policies.csv"
    df_valid.to_csv(combined_path, index=False)
    written["combined_policies"] = str(combined_path)

    # 2. Individual trace - ALL rows
    if df_final_individual is not None and not df_final_individual.empty:
        ind_path = output_dir / "trace_individual_policies.csv"
        df_final_individual.to_csv(ind_path, index=False)
        written["trace_individual"] = str(ind_path)

        # 2b. Filtered: only final_verdict == True
        if "final_verdict" in df_final_individual.columns:
            df_valid_ind = df_final_individual[df_final_individual["final_verdict"] == True]
            ind_valid_path = output_dir / "trace_individual_policies_valid.csv"
            df_valid_ind.to_csv(ind_valid_path, index=False)
            written["trace_individual_valid"] = str(ind_valid_path)

    # 3. Initiative trace - ALL rows
    if df_initiatives is not None and not df_initiatives.empty:
        init_path = output_dir / "trace_initiative_policies.csv"
        df_initiatives.to_csv(init_path, index=False)
        written["trace_initiatives"] = str(init_path)

        # 3b. Filtered: only final_verdict == True
        if "final_verdict" in df_initiatives.columns:
            df_valid_init = df_initiatives[df_initiatives["final_verdict"] == True]
            init_valid_path = output_dir / "trace_initiative_policies_valid.csv"
            df_valid_init.to_csv(init_valid_path, index=False)
            written["trace_initiatives_valid"] = str(init_valid_path)

    return written
