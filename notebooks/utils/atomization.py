"""Atomization manifest: split bundle rows before classification with stable row keys."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

import pandas as pd

_MANIFEST_VERSION = 1


def source_bundle_key(
    city: str,
    policy_statement: str,
    role: Optional[str] = None,
    parent_statement: Optional[str] = None,
) -> str:
    """Stable identifier for one valid-row / combined-row before classification."""
    c = str(city or "").strip()
    stmt = str(policy_statement or "").strip()
    rl = str(role or "individual").strip()
    par = str(parent_statement or "").strip()
    digest = hashlib.sha256(f"{c}|{rl}|{par}|{stmt}".encode("utf-8")).hexdigest()[:24]
    return f"{c}::{digest}"


def structural_anchor_needed(record: dict, all_records: list[dict]) -> bool:
    """True if dropping this policy_statement would strand subs referencing it."""
    bundle_stmt = str(record.get("policy_statement") or "")
    role = str(record.get("role") or "").lower()
    city = str(record.get("__city_key") or record.get("city") or "")
    if role == "parent":
        return True
    prefix = bundle_stmt[:80] if bundle_stmt else ""
    for r in all_records:
        if str(r.get("__city_key") or r.get("city") or "") != city:
            continue
        ps = str(r.get("parent_statement") or "")
        if not ps:
            continue
        if bundle_stmt and (ps == bundle_stmt or (prefix and ps.startswith(prefix))):
            return True
    return False


def load_manifest(path: str | Path) -> list[dict[str, Any]]:
    """Load reviewed manifest JSON. Returns list of entry dicts."""
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "entries" in raw:
        return list(raw["entries"])
    if isinstance(raw, list):
        return raw
    raise ValueError(f"manifest must be {{\"entries\": [...]}} or a list — got keys {type(raw)}")


def _norm_atom(atom: Any) -> dict[str, Any]:
    if isinstance(atom, dict):
        return atom
    return {"policy_statement": str(atom), "deadline": None, "mechanism": "", "target": None}


def apply_atomization_preclass(
    records: list[dict],
    entries: list[dict[str, Any]],
) -> list[dict]:
    """Expand manifest entries on pooled pre-class records (before Step 8). Mutates-ish via deepcopy."""
    if not entries:
        for r in records:
            c = str(r.get("__city_key") or "")
            rk = source_bundle_key(
                c,
                str(r.get("policy_statement") or ""),
                r.get("role"),
                r.get("parent_statement"),
            )
            r.setdefault("row_key", rk)
            r.setdefault("source_bundle_key", rk)
            r.setdefault("is_atomized_row", False)
            r.setdefault("is_decomposed", False)
        return records

    by_key = {e["source_bundle_key"]: e for e in entries if e.get("source_bundle_key")}
    used: set[str] = set()
    out: list[dict] = []

    for r in records:
        c = str(r.get("__city_key") or "")
        rk = source_bundle_key(
            c,
            str(r.get("policy_statement") or ""),
            r.get("role"),
            r.get("parent_statement"),
        )
        r["row_key"] = rk
        r["source_bundle_key"] = rk

        if rk not in by_key:
            r.setdefault("is_atomized_row", False)
            r.setdefault("is_decomposed", False)
            out.append(r)
            continue

        if rk in used:
            # duplicate source row — drop extra copy
            continue
        used.add(rk)

        entry = by_key[rk]
        atoms = [_norm_atom(a) for a in (entry.get("atoms") or [])]
        atoms = [a for a in atoms if str(a.get("policy_statement") or "").strip()]
        bundle_stmt = str(r.get("policy_statement") or "")
        mode = str(entry.get("mode") or "replace").strip().lower()
        vt = str(r.get("verbatim_text") or "")

        if not atoms:
            r.setdefault("is_atomized_row", False)
            r.setdefault("is_decomposed", False)
            out.append(r)
            continue

        if mode == "anchor_parent":
            parent = copy.deepcopy(r)
            parent["row_key"] = rk
            parent["source_bundle_key"] = rk
            parent["is_decomposed"] = True
            parent["entity_cluster_skip"] = True
            parent["is_atomized_row"] = False
            parent["source_bundle_policy_statement"] = bundle_stmt
            parent["atom_id"] = None
            parent["parent_row_id"] = None
            out.append(parent)

            for j, atom in enumerate(atoms):
                ps_atom = str(atom.get("policy_statement") or "").strip()
                ar = copy.deepcopy(r)
                ar["policy_statement"] = ps_atom
                ar["verbatim_text"] = vt
                ar["is_atomized_row"] = True
                ar["is_decomposed"] = False
                ar["entity_cluster_skip"] = False
                ar["row_key"] = f"{rk}::atom::{j}"
                ar["source_bundle_key"] = rk
                ar["atom_id"] = f"{rk}::atom::{j}"
                ar["parent_row_id"] = rk
                ar["source_bundle_policy_statement"] = bundle_stmt
                dl = atom.get("deadline")
                if dl:
                    prev = str(ar.get("instance_edge_case_notes") or "").strip()
                    suf = f" [atom_deadline_from_bundle: {dl}]"
                    ar["instance_edge_case_notes"] = (prev + suf).strip()
                out.append(ar)
            continue

        # replace mode
        for j, atom in enumerate(atoms):
            ps_atom = str(atom.get("policy_statement") or "").strip()
            ar = copy.deepcopy(r)
            ar["policy_statement"] = ps_atom
            ar["verbatim_text"] = vt
            ar["is_atomized_row"] = True
            ar["is_decomposed"] = False
            ar["entity_cluster_skip"] = False
            ar["row_key"] = f"{rk}::atom::{j}"
            ar["source_bundle_key"] = rk
            ar["atom_id"] = f"{rk}::atom::{j}"
            ar["parent_row_id"] = rk
            ar["source_bundle_policy_statement"] = bundle_stmt
            dl = atom.get("deadline")
            if dl:
                prev = str(ar.get("instance_edge_case_notes") or "").strip()
                suf = f" [atom_deadline_from_bundle: {dl}]"
                ar["instance_edge_case_notes"] = (prev + suf).strip()
            out.append(ar)

    return out


def row_key_from_combined_row(city: str, row: dict) -> str:
    return source_bundle_key(
        city,
        str(row.get("policy_statement") or ""),
        row.get("role"),
        row.get("parent_statement"),
    )


def apply_atomization_combined_df(
    df: pd.DataFrame,
    city: str,
    entries: list[dict[str, Any]],
) -> pd.DataFrame:
    """Split rows in minimal combined_policies dataframe to mirror pre-class atomization."""
    if df.empty:
        return df

    def _stamp_defaults(frame: pd.DataFrame) -> pd.DataFrame:
        rks = [row_key_from_combined_row(city, row.to_dict()) for _, row in frame.iterrows()]
        out_df = frame.copy()
        out_df["row_key"] = rks
        out_df["source_bundle_key"] = rks
        return out_df

    if not entries:
        return _stamp_defaults(df) if "row_key" not in df.columns else df

    city_entries = []
    if entries and isinstance(entries[0], dict) and entries[0].get("city") is not None:
        city_entries = [e for e in entries if str(e.get("city") or "") == str(city)]
    else:
        city_entries = list(entries)

    by_key = {e["source_bundle_key"]: e for e in city_entries if e.get("source_bundle_key")}
    used: set[str] = set()

    rows_out: list[dict] = []
    for _, row in df.iterrows():
        d = row.to_dict()
        rk = row_key_from_combined_row(city, d)

        if rk not in by_key:
            d["row_key"] = rk
            d["source_bundle_key"] = rk
            rows_out.append(d)
            continue
        if rk in used:
            continue
        used.add(rk)

        entry = by_key[rk]
        atoms = [_norm_atom(a) for a in (entry.get("atoms") or [])]
        atoms = [a for a in atoms if str(a.get("policy_statement") or "").strip()]
        mode = str(entry.get("mode") or "replace").strip().lower()
        vt = str(d.get("verbatim_text") or "")
        bundle_stmt = str(d.get("policy_statement") or "")

        if not atoms:
            d["row_key"] = rk
            d["source_bundle_key"] = rk
            rows_out.append(d)
            continue

        if mode == "anchor_parent":
            p = dict(d)
            p["row_key"] = rk
            p["source_bundle_key"] = rk
            p["is_decomposed"] = True
            p["is_atomized_row"] = False
            p["source_bundle_policy_statement"] = bundle_stmt
            p["atom_id"] = None
            p["parent_row_id"] = None
            rows_out.append(p)

            for j, atom in enumerate(atoms):
                ps_atom = str(atom.get("policy_statement") or "").strip()
                if not ps_atom:
                    continue
                ar = dict(d)
                ar["policy_statement"] = ps_atom
                ar["verbatim_text"] = vt
                ar["row_key"] = f"{rk}::atom::{j}"
                ar["source_bundle_key"] = rk
                ar["validation_inherited_from_bundle"] = True
                ar["is_atomized_row"] = True
                ar["atom_id"] = f"{rk}::atom::{j}"
                ar["parent_row_id"] = rk
                ar["source_bundle_policy_statement"] = bundle_stmt
                rows_out.append(ar)
            continue

        for j, atom in enumerate(atoms):
            ps_atom = str(atom.get("policy_statement") or "").strip()
            if not ps_atom:
                continue
            ar = dict(d)
            ar["policy_statement"] = ps_atom
            ar["verbatim_text"] = vt
            ar["row_key"] = f"{rk}::atom::{j}"
            ar["source_bundle_key"] = rk
            ar["validation_inherited_from_bundle"] = True
            ar["is_atomized_row"] = True
            ar["atom_id"] = f"{rk}::atom::{j}"
            ar["parent_row_id"] = rk
            ar["source_bundle_policy_statement"] = bundle_stmt
            rows_out.append(ar)

    out_df = pd.DataFrame(rows_out)
    return out_df


def build_manifest_entries_from_classified_lookup(
    llm_report_rows: list[dict],
    *,
    classified_rows_ordered: Optional[list[dict]] = None,
    classified_concat_df: Optional[pd.DataFrame] = None,
) -> list[dict[str, Any]]:
    """Produce manifest entries from LLM bundling rows + classified metadata (exact city+statement match).

    Uses ``row_index`` in report when paired with classified_rows_ordered of same ordering.
    """
    entries: list[dict[str, Any]] = []

    for item in llm_report_rows:
        if not bool(item.get("is_bundled")) or item.get("relationship") != "bundle_distinct":
            continue
        city = str(item.get("city") or "")
        ps = str(item.get("policy_statement") or "").strip()
        atoms_raw = item.get("atomic_commitments") or []

        meta: Optional[dict] = None
        if classified_rows_ordered is not None:
            rid = item.get("row_index")
            if isinstance(rid, int) and 0 <= rid < len(classified_rows_ordered):
                meta = classified_rows_ordered[int(rid)]
        if meta is None and classified_concat_df is not None:
            m = classified_concat_df
            if "city" in m.columns and "policy_statement" in m.columns:
                cand = m[
                    (m["city"].astype(str) == city)
                    & (m["policy_statement"].astype(str).str.strip() == ps)
                ]
                if not cand.empty:
                    meta = cand.iloc[0].to_dict()

        role = "individual"
        parent_statement = None
        if meta is not None:
            role = meta.get("role") or "individual"
            parent_statement = meta.get("parent_statement")

        rk = source_bundle_key(city, ps, role, parent_statement)

        if classified_rows_ordered is not None:
            idx = item.get("row_index")
            if isinstance(idx, int) and 0 <= idx < len(classified_rows_ordered):
                rec = classified_rows_ordered[int(idx)]
                anchor = structural_anchor_needed(rec, classified_rows_ordered)
            else:
                anchor = False
        elif classified_concat_df is not None:
            anchor = structural_anchor_needed_from_concat(
                classified_concat_df, city, ps, str(role),
            )
        else:
            anchor = False

        entries.append(
            {
                "source_bundle_key": rk,
                "city": city,
                "policy_statement": ps,
                "role": role,
                "parent_statement": parent_statement,
                "mode": "anchor_parent" if anchor else "replace",
                "atoms": [_norm_atom(a) for a in atoms_raw],
            }
        )

    return entries


def structural_anchor_needed_from_concat(
    df: pd.DataFrame, city: str, bundle_statement: str, role: str
) -> bool:
    if str(role).lower() == "parent":
        return True
    bundle_statement = str(bundle_statement or "").strip()
    prefix = bundle_statement[:80] if bundle_statement else ""
    m = df[df.get("city", pd.Series(dtype=str)).astype(str) == str(city)]
    if m.empty:
        return False
    for _, rr in m.iterrows():
        ps = str(rr.get("parent_statement") or "")
        if not ps:
            continue
        if bundle_statement and (
            ps == bundle_statement or (prefix and ps.startswith(prefix))
        ):
            return True
    return False


def write_manifest(entries: list[dict[str, Any]], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"manifest_version": _MANIFEST_VERSION, "entries": entries},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
