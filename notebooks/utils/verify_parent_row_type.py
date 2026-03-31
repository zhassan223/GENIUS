from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import pandas as pd

VALID_PARENT_ROW_TYPES = {
    "structural header",
    "standalone parent policy",
}


def _safe_read_csv(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except Exception as exc:
        print(f"[warn] could not read {path}: {exc}")
        return None


def _count_filled(series: pd.Series) -> int:
    return int(series.notna().sum())


def _count_valid_parent_row_types(series: pd.Series) -> int:
    return int(series.isin(VALID_PARENT_ROW_TYPES).sum())


def _initiative_row_mask(df: pd.DataFrame) -> pd.Series:
    if "role" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["role"].isin(["parent", "sub"])


def _city_dirs(outputs_root: Path) -> Iterable[Path]:
    for path in sorted(outputs_root.iterdir()):
        if path.is_dir():
            yield path


def verify(outputs_root: Path, master_csv: Path | None = None) -> int:
    failures = 0

    print("Per-city files")
    print("=" * 80)
    for city_dir in _city_dirs(outputs_root):
        city = city_dir.name
        init_trace = _safe_read_csv(city_dir / "trace_initiative_policies.csv")
        init_valid = _safe_read_csv(city_dir / "trace_initiative_policies_valid.csv")
        combined = _safe_read_csv(city_dir / "combined_policies.csv")

        if init_trace is None and init_valid is None and combined is None:
            continue

        print(f"\n[{city}]")

        combined_initiative_rows = 0
        if combined is not None:
            combined_initiative_rows = int(_initiative_row_mask(combined).sum())
        city_has_initiatives = (
            (init_trace is not None and len(init_trace) > 0)
            or (init_valid is not None and len(init_valid) > 0)
            or combined_initiative_rows > 0
        )

        if not city_has_initiatives:
            print("  no initiative rows detected")
            continue

        for label, df in (
            ("trace_initiative_policies.csv", init_trace),
            ("trace_initiative_policies_valid.csv", init_valid),
        ):
            if df is None:
                print(f"  {label}: missing")
                continue
            if "parent_row_type" not in df.columns:
                print(f"  {label}: column missing")
                failures += 1
                continue
            filled = _count_filled(df["parent_row_type"])
            valid = _count_valid_parent_row_types(df["parent_row_type"])
            total = len(df)
            print(f"  {label}: {filled}/{total} filled, {valid}/{total} valid")
            if total > 0 and (filled < total or valid < total):
                failures += 1

        if combined is None:
            print("  combined_policies.csv: missing")
        elif "parent_row_type" not in combined.columns:
            print("  combined_policies.csv: column missing")
            failures += 1
        else:
            initiative_rows = _initiative_row_mask(combined)
            total = int(initiative_rows.sum())
            combined_parent_row_types = combined.loc[initiative_rows, "parent_row_type"]
            filled = _count_filled(combined_parent_row_types)
            valid = _count_valid_parent_row_types(combined_parent_row_types)
            print(f"  combined_policies.csv (parent/sub rows): {filled}/{total} filled, {valid}/{total} valid")
            if total > 0 and (filled < total or valid < total):
                failures += 1

    if master_csv is not None:
        print("\nMaster file")
        print("=" * 80)
        master = _safe_read_csv(master_csv)
        if master is None:
            print(f"{master_csv}: missing")
            failures += 1
        elif "parent_row_type" not in master.columns:
            print(f"{master_csv.name}: column missing")
            failures += 1
        elif "city" not in master.columns:
            print(f"{master_csv.name}: missing city column")
            failures += 1
        else:
            initiative_rows = _initiative_row_mask(master)
            master_initiative_rows = master.loc[initiative_rows].copy()
            master_initiative_rows["parent_row_type_filled"] = master_initiative_rows["parent_row_type"].notna()
            master_initiative_rows["parent_row_type_valid"] = master_initiative_rows["parent_row_type"].isin(
                VALID_PARENT_ROW_TYPES
            )
            summary = (
                master_initiative_rows
                .groupby("city", dropna=False)["parent_row_type_filled"]
                .agg(["sum", "count"])
                .rename(columns={"sum": "filled", "count": "total"})
                .reset_index()
            )
            valid_summary = (
                master_initiative_rows
                .groupby("city", dropna=False)["parent_row_type_valid"]
                .sum()
                .reset_index(name="valid")
            )
            summary = summary.merge(valid_summary, on="city", how="left")
            if summary.empty:
                print("No parent/sub rows found in master CSV.")
            else:
                for row in summary.itertuples(index=False):
                    print(f"{row.city}: {int(row.filled)}/{int(row.total)} filled, {int(row.valid)}/{int(row.total)} valid")
                    if int(row.total) > 0 and (int(row.filled) < int(row.total) or int(row.valid) < int(row.total)):
                        failures += 1

    print("\nResult")
    print("=" * 80)
    if failures:
        print(f"parent_row_type verification failed with {failures} issue(s).")
        return 1

    print("parent_row_type is fully populated everywhere it is expected.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify parent_row_type propagation in per-city and master CSV outputs."
    )
    parser.add_argument(
        "--outputs-root",
        default="notebooks/outputs",
        help="Path to the outputs directory.",
    )
    parser.add_argument(
        "--master-csv",
        default="notebooks/outputs/all_cities_valid_policies.csv",
        help="Path to the stacked master valid policies CSV.",
    )
    args = parser.parse_args()

    outputs_root = Path(args.outputs_root)
    master_csv = Path(args.master_csv) if args.master_csv else None
    return verify(outputs_root=outputs_root, master_csv=master_csv)


if __name__ == "__main__":
    raise SystemExit(main())
