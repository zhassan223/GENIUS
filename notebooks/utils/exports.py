from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd


def _norm_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def build_combined_policies_table(
    *,
    df_policies: pd.DataFrame,
    df_final_individual: Optional[pd.DataFrame] = None,
    df_initiatives: Optional[pd.DataFrame] = None,
    policy_clusters: Optional[list] = None,
) -> pd.DataFrame:
    """
    Combine:
      - df_policies: all parent/sub/individual policy rows (from clusters_to_records)
      - df_final_individual: individual-only validation metrics (df_final)
      - df_initiatives: initiative-level validation (one row per parent initiative)

    Returns a single DataFrame with trace references:
      - trace_type, trace_id, trace_path
    """

    combined = df_policies.copy()

    if "cluster_id" in combined.columns:
        combined["cluster_id"] = pd.to_numeric(
            combined["cluster_id"], errors="coerce"
        ).astype("Int64")

    for col in ["section_header", "role", "policy_statement", "verbatim_text"]:
        if col in combined.columns:
            combined[col] = _norm_text(combined[col])

    # -----------------------------
    # Attach INDIVIDUAL validation
    # -----------------------------
    if df_final_individual is not None and len(df_final_individual) > 0:
        df_final = df_final_individual.copy()
        if "cluster_id" in df_final.columns:
            df_final["cluster_id"] = pd.to_numeric(
                df_final["cluster_id"], errors="coerce"
            ).astype("Int64")

        for col in ["section_header", "role", "policy_statement", "verbatim_text"]:
            if col in df_final.columns:
                df_final[col] = _norm_text(df_final[col])

        ind_cols_wanted = [
            "validation_result",
            "confidence_score",
            "validation_reasoning",
            "final_verdict",
            "has_quantifiable_target",
            "has_timeline",
            "has_binding_mechanism",
            "has_spatial_specificity",
            "weak_language_detected",
            "strong_language_detected",
        ]
        ind_available = [c for c in ind_cols_wanted if c in df_final.columns]

        merge_keys = [
            k
            for k in ["cluster_id", "role", "policy_statement", "verbatim_text"]
            if k in combined.columns and k in df_final.columns
        ]
        if merge_keys:
            combined = combined.merge(
                df_final[merge_keys + ind_available],
                on=merge_keys,
                how="left",
                suffixes=("", "_ind"),
            )
        else:
            fallback_keys = [
                k
                for k in ["policy_statement", "verbatim_text"]
                if k in combined.columns and k in df_final.columns
            ]
            combined = combined.merge(
                df_final[fallback_keys + ind_available], on=fallback_keys, how="left"
            )

    # -----------------------------
    # Attach INITIATIVE validation
    # -----------------------------
    if df_initiatives is not None and len(df_initiatives) > 0:
        init = df_initiatives.copy()
        if "initiative_name" in init.columns:
            init["initiative_name"] = _norm_text(init["initiative_name"])

        init_cols_wanted = [
            "initiative_name",
            "initiative_result",
            "confidence_score",
            "coverage_score",
            "coherence_score",
            "aggregate_measurability",
            "has_implementation_pathway",
            "inherited_binding_mechanism",
            "inherited_spatial_scope",
            "subs_strong",
            "subs_moderate",
            "subs_weak",
            "final_verdict",
            "initiative_reasoning",
            "sub_assessments",
        ]
        init_available = [c for c in init_cols_wanted if c in init.columns]

        combined = combined.merge(
            init[init_available],
            left_on="section_header",
            right_on="initiative_name",
            how="left",
            suffixes=("", "_init"),
        )

    # -----------------------------
    # Trace indexing helpers
    # -----------------------------
    combined["trace_type"] = None
    combined["trace_id"] = None
    combined["trace_path"] = None

    sub_index_map: Dict[Tuple[str, str], int] = {}
    if policy_clusters is not None:
        for cluster in policy_clusters:
            if cluster.get("cluster_type") != "parent_with_subs":
                continue
            header = str(cluster.get("section_header", "")).strip()
            subs = cluster.get("subs", []) or []
            for i, sub in enumerate(subs, 1):
                stmt = str(getattr(sub, "policy_statement", "")).strip()
                sub_index_map[(header, stmt)] = i

    if sub_index_map:
        combined["sub_index"] = combined.apply(
            lambda r: sub_index_map.get(
                (str(r.get("section_header", "")).strip(), str(r.get("policy_statement", "")).strip())
            ),
            axis=1,
        )
    else:
        combined["sub_index"] = None

    # Attach per-sub assessment columns when possible
    if (
        df_initiatives is not None
        and len(df_initiatives) > 0
        and "sub_assessments" in df_initiatives.columns
        and "sub_index" in combined.columns
    ):
        sub_rows: List[dict] = []
        for _, r in df_initiatives.iterrows():
            header = str(r.get("initiative_name", "")).strip()
            sa = r.get("sub_assessments")

            if isinstance(sa, str):
                try:
                    sa = json.loads(sa)
                except Exception:
                    try:
                        sa = ast.literal_eval(sa)
                    except Exception:
                        sa = None

            if not isinstance(sa, list):
                continue

            for i, entry in enumerate(sa, 1):
                if not isinstance(entry, dict):
                    continue
                sub_rows.append(
                    {
                        "section_header": header,
                        "sub_index": i,
                        "sub_action_label": entry.get("action_label"),
                        "sub_strength": entry.get("strength"),
                        "sub_has_quantifiable_target": entry.get("has_quantifiable_target"),
                        "sub_has_timeline": entry.get("has_timeline"),
                        "sub_is_concrete_action": entry.get("is_concrete_action"),
                    }
                )

        if sub_rows:
            df_sub = pd.DataFrame(sub_rows)
            combined = combined.merge(df_sub, on=["section_header", "sub_index"], how="left")

    # Assign trace IDs/types (paths filled in by export function)
    is_ind = combined.get("role", "") == "individual"
    is_init = combined.get("role", "").isin(["parent", "sub"])

    combined.loc[is_ind, "trace_type"] = "individual"
    if "cluster_id" in combined.columns:
        combined.loc[is_ind, "trace_id"] = combined.loc[is_ind, "cluster_id"].apply(
            lambda x: f"ind:{x}"
        )

    combined.loc[is_init, "trace_type"] = "initiative"

    def _init_trace_id(row) -> Optional[str]:
        header = str(row.get("section_header", "")).strip()
        role = str(row.get("role", "")).strip()
        if role == "parent":
            return f"init:{header}:parent"
        if role == "sub":
            si = row.get("sub_index")
            return f"init:{header}:sub:{int(si) if pd.notna(si) else 'unknown'}"
        return None

    combined.loc[is_init, "trace_id"] = combined.loc[is_init].apply(_init_trace_id, axis=1)

    return combined


def export_combined_table_and_traces(
    *,
    combined: pd.DataFrame,
    df_initiatives: Optional[pd.DataFrame],
    output_dir: str | Path = "outputs/policy_pipeline_v4",
) -> dict:
    """
    Write:
      - all_policies_combined.csv
      - traces/initiative_parent_sub_traces.jsonl
      - traces/individual_policy_traces.jsonl

    Returns paths.
    """

    output_dir = Path(output_dir)
    traces_dir = output_dir / "traces"
    output_dir.mkdir(parents=True, exist_ok=True)
    traces_dir.mkdir(parents=True, exist_ok=True)

    combined_table_path = output_dir / "all_policies_combined.csv"
    init_traces_path = traces_dir / "initiative_parent_sub_traces.jsonl"
    ind_traces_path = traces_dir / "individual_policy_traces.jsonl"

    # Fill trace_path based on role/type
    combined = combined.copy()
    combined.loc[combined.get("trace_type", "") == "individual", "trace_path"] = str(
        ind_traces_path
    )
    combined.loc[combined.get("trace_type", "") == "initiative", "trace_path"] = str(
        init_traces_path
    )

    # Individual traces
    ind_cols = [
        "trace_id",
        "cluster_id",
        "section_header",
        "policy_statement",
        "validation_result",
        "confidence_score",
        "final_verdict",
        "validation_reasoning",
    ]
    ind_cols = [c for c in ind_cols if c in combined.columns]
    df_ind = combined[combined.get("role", "") == "individual"][ind_cols].copy()
    with ind_traces_path.open("w", encoding="utf-8") as f:
        for rec in df_ind.to_dict(orient="records"):
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    # Initiative traces (parent reasoning + per-sub assessment objects)
    init_trace_records: List[dict] = []
    if df_initiatives is not None and len(df_initiatives) > 0 and "initiative_name" in df_initiatives.columns:
        for _, row in df_initiatives.iterrows():
            header = str(row.get("initiative_name", "")).strip()
            init_trace_records.append(
                {
                    "trace_id": f"init:{header}:parent",
                    "trace_type": "initiative_parent",
                    "initiative_name": header,
                    "initiative_result": row.get("initiative_result"),
                    "confidence_score": row.get("confidence_score"),
                    "initiative_reasoning": row.get("initiative_reasoning"),
                }
            )

            sub_assessments = row.get("sub_assessments")
            if isinstance(sub_assessments, str):
                try:
                    sub_assessments = json.loads(sub_assessments)
                except Exception:
                    try:
                        sub_assessments = ast.literal_eval(sub_assessments)
                    except Exception:
                        sub_assessments = None

            if isinstance(sub_assessments, list):
                for i, sa in enumerate(sub_assessments, 1):
                    init_trace_records.append(
                        {
                            "trace_id": f"init:{header}:sub:{i}",
                            "trace_type": "initiative_sub_assessment",
                            "initiative_name": header,
                            "sub_index": i,
                            "sub_assessment": sa,
                            "initiative_reasoning": row.get("initiative_reasoning"),
                        }
                    )

    with init_traces_path.open("w", encoding="utf-8") as f:
        for rec in init_trace_records:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    # Keep combined table readable (drop nested sub_assessments if present)
    if "sub_assessments" in combined.columns:
        combined = combined.drop(columns=["sub_assessments"])

    combined.to_csv(combined_table_path, index=False)

    return {
        "combined_table": str(combined_table_path),
        "initiative_traces": str(init_traces_path),
        "individual_traces": str(ind_traces_path),
    }

