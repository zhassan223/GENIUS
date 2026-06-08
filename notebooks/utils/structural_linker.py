"""
F7b — Section-bullet demote.

Deterministic structural relabel: consecutive `role=parent` rows that share a
non-empty `section_header` are collapsed so one stays parent and the rest
become `role=sub` under that parent's `policy_statement` (stored as
`parent_policy_name`). No rows added or removed.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _doc_key(record: dict) -> str:
    v = record.get("doc_id") or record.get("__city_key") or record.get("location")
    if v is None:
        return ""
    return str(v)


def _section_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value != value:  # NaN
        return ""
    s = str(value).strip()
    if s.lower() == "nan":
        return ""
    return s


def _is_parent(record: dict) -> bool:
    return str(record.get("role") or "").strip().lower() == "parent"


def _introduces_section_header(record: dict, section_header: str) -> bool:
    """Heuristic: section-introducing rows usually echo the heading in policy_statement."""
    ps = str(record.get("policy_statement") or "").strip().lower()
    if not ps:
        return False
    label = " ".join(section_header.lower().replace("_", " ").split())
    if not label:
        return False
    return ps.startswith(label) or ps.startswith(label + ":")


def _parent_choice_key(record: dict, section_header: str) -> tuple[int, int, int]:
    verbatim = str(record.get("verbatim_text") or "")
    stmt = str(record.get("policy_statement") or "")
    # Prefer the row whose policy_statement opens with the section heading (the
    # umbrella line), then longest verbatim_text, then longest policy_statement.
    return (
        int(_introduces_section_header(record, section_header)),
        len(verbatim),
        len(stmt),
    )


def _backfill_parent_policy_name_for_subs(records: list[dict]) -> None:
    """Set parent_policy_name from parent_statement when missing (subs only).

    Resolver-linked subs already carry the parent's statement in
    `parent_statement`. Downstream checks key off `parent_policy_name`; this
    backfill is a no-op when F7b just demoted (those rows already have the
    field) and keeps dry-runs consistent when the role/sub linkage pre-exists.
    """
    for r in records:
        if str(r.get("role") or "").strip().lower() != "sub":
            continue
        existing = r.get("parent_policy_name")
        if existing is not None and str(existing).strip():
            continue
        src = r.get("parent_statement")
        if src is None or (isinstance(src, float) and src != src):  # NaN
            continue
        s = str(src).strip()
        if s:
            r["parent_policy_name"] = s


def _process_document_records(doc_records: list[dict]) -> None:
    i = 0
    n = len(doc_records)
    while i < n:
        rec = doc_records[i]
        if not _is_parent(rec):
            i += 1
            continue
        section = _section_str(rec.get("section_header"))
        if not section:
            i += 1
            continue
        j = i + 1
        while j < n:
            nxt = doc_records[j]
            if not _is_parent(nxt):
                break
            if _section_str(nxt.get("section_header")) != section:
                break
            j += 1
        run = doc_records[i:j]
        eligible = [
            r
            for r in run
            if not r.get("f7b_demoted_from_parent") and _is_parent(r)
        ]
        if len(eligible) >= 2:
            chosen = max(eligible, key=lambda r: _parent_choice_key(r, section))
            parent_stmt = chosen["policy_statement"]
            reason = (
                f"shared section_header {section!r} with parent of larger verbatim"
            )
            for r in eligible:
                if r is chosen:
                    continue
                r["role"] = "sub"
                r["parent_policy_name"] = parent_stmt
                r["f7b_demoted_from_parent"] = True
                r["f7b_demote_reason"] = reason
        i = j


def apply_F7b_section_bullet_demote(records: list[dict]) -> list[dict]:
    """Demote consecutive same-section parents to subs under one chosen parent.

    Mutates records in place and returns the list. Idempotent: running twice
    is a no-op (rows already demoted have role='sub' and won't be re-evaluated).

    Selection of which row stays parent: prefer the row whose policy_statement
    opens with the section heading, then longest verbatim_text, then longest
    policy_statement.

    Adds these fields to demoted rows:
      - role:                       set to 'sub'
      - parent_policy_name:         set to the chosen parent's policy_statement
      - f7b_demoted_from_parent:    True
      - f7b_demote_reason:          string explaining why
    """
    by_doc: dict[str, list[int]] = defaultdict(list)
    for idx, rec in enumerate(records):
        by_doc[_doc_key(rec)].append(idx)

    for _dk, indices in by_doc.items():
        doc_records = [records[k] for k in indices]
        _process_document_records(doc_records)

    _backfill_parent_policy_name_for_subs(records)

    return records


def apply_F7b_to_records(records: list[dict]) -> list[dict]:
    """Public wrapper. Same as above; named for consistency with F1/F2/F4
    helpers (apply_F1_F4_to_records, apply_language_filter_to_records)."""
    return apply_F7b_section_bullet_demote(records)
