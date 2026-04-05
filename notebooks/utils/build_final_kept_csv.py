from __future__ import annotations

from pathlib import Path

import pandas as pd


def build_final_csv(
    *,
    input_csv: str | Path,
    output_csv: str | Path,
) -> Path:
    input_csv = Path(input_csv)
    output_csv = Path(output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(input_csv)

    wanted_cols = [
        "city",
        "cluster_type",
        "role",
        "policy_statement",
        "parent_statement",
        "parent_row_type",
        "verbatim_text",
        "canonical_mechanism",
        "mechanism_description",
        "secondary_categories",
        "is_financial_instrument",
        "primary_category",
        "climate_relevance",
    ]

    missing = [c for c in wanted_cols if c not in df.columns]
    if missing:
        raise ValueError(
            "Missing required columns in input CSV: "
            f"{missing}. Available columns: {list(df.columns)}"
        )

    out = df.loc[:, wanted_cols].copy()
    out.to_csv(output_csv, index=False)
    return output_csv


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[2]
    input_csv = repo_root / "notebooks" / "outputs" / "all_cities_kept_classified_policies.csv"
    output_csv = (
        repo_root
        / "notebooks"
        / "outputs"
        / "all_cities_kept_classified_policies_final.csv"
    )

    written = build_final_csv(input_csv=input_csv, output_csv=output_csv)
    print(f"Wrote {written}")
