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
    Build a minimal combined table directly from policy_clusters.

    Every row (individual / parent / sub) gets the same 9 columns.
    All validation detail lives in the separate trace files.
    """
    if policy_clusters is None:
        raise ValueError("policy_clusters is required to build the combined table.")

    rows: List[dict] = []

    for cluster in policy_clusters:
        ctype = cluster.get("cluster_type", "")
        cid   = cluster.get("cluster_id")

        if ctype == "individual":
            p = cluster.get("policy")
            if p is None:
                continue
            d = p.model_dump() if hasattr(p, "model_dump") else dict(p)
            rows.append({
                "cluster_id":           cid,
                "cluster_type":         ctype,
                "role":                 "individual",
                "sector":               d.get("sector"),
                "section_header":       d.get("section_header"),
                "policy_statement":     d.get("policy_statement"),
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

    return pd.DataFrame(rows, columns=_COMBINED_COLS)


def export_combined_table_and_traces(
    *,
    combined: pd.DataFrame,
    df_initiatives: Optional[pd.DataFrame],
    df_final_individual: Optional[pd.DataFrame] = None,
    output_dir: str | Path = "outputs/policy_pipeline_v4",
) -> dict:
    """
    Write:
      - combined_policies.csv                  <- minimal 9-column table (all roles)
      - trace_individual_policies.csv          <- ALL individual validation details
      - trace_individual_policies_valid.csv    <- only final_verdict == True
      - trace_initiative_policies.csv          <- ALL initiative validation details
      - trace_initiative_policies_valid.csv    <- only final_verdict == True

    Returns a dict mapping label -> path string.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written: dict = {}

    # 1. Minimal combined table
    combined_path = output_dir / "combined_policies.csv"
    combined.to_csv(combined_path, index=False)
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
