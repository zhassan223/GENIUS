from __future__ import annotations

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
    "verbatim_text",
    "extraction_rationale",
]


def build_combined_policies_table(
    *,
    df_policies: pd.DataFrame,
    df_final_individual: Optional[pd.DataFrame] = None,
    df_initiatives: Optional[pd.DataFrame] = None,
    policy_clusters: Optional[list] = None,
) -> pd.DataFrame:
    """
    Build a minimal combined table from policy_clusters + df_final_individual.

    Every row (individual / parent / sub) gets the same 9 columns.
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

    for cluster in policy_clusters:
        ctype = cluster.get("cluster_type", "")
        cid   = cluster.get("cluster_id")

        if ctype in ("individual", "orphan_sub"):
            p = cluster.get("policy")
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
                "verbatim_text":        d.get("verbatim_text"),
                "extraction_rationale": d.get("extraction_rationale"),
            })

        elif ctype == "parent_with_subs":
            parent = cluster.get("parent")
            subs   = cluster.get("subs") or []
            if parent is None:
                continue
            pd_ = parent.model_dump() if hasattr(parent, "model_dump") else dict(parent)

            rows.append({
                "cluster_id":           cid,
                "cluster_type":         ctype,
                "role":                 "parent",
                "sector":               pd_.get("sector"),
                "section_header":       pd_.get("section_header"),
                "policy_statement":     pd_.get("policy_statement"),
                "parent_statement":     None,
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
                    "parent_statement":     pd_.get("policy_statement"),
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
