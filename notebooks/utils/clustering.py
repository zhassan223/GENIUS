from __future__ import annotations

import re
from typing import Dict, List

from .schemas import ExtractedPolicy


def _normalize_header(header: str) -> str:
    """Normalize a section header for display consistency.

    Strips whitespace, collapses runs, lowercases, and removes trailing
    punctuation so that minor LLM formatting differences don't affect display.
    """
    h = re.sub(r"\s+", " ", header).strip().lower()
    h = re.sub(r"[.:;,]+$", "", h)
    return h


def cluster_policies(policies: List[ExtractedPolicy]) -> List[dict]:
    """
    Cluster policies using the resolver's parent_policy_name linkage.

    The resolver (dspy_resolve.py) already spent 3 stages linking subs to
    parents via parent_policy_name. This function respects that linkage
    instead of re-grouping by section_header.

    Algorithm:
    1. Index all parents by policy_statement
    2. Each sub with a parent_policy_name that matches a parent → grouped
       under that parent
    3. Subs with no match → orphan_sub clusters
    4. Individuals → individual clusters
    5. Parents with no subs still get a parent_with_subs cluster (empty subs)
    """

    # Index parents by policy_statement for O(1) lookup
    parent_by_stmt: Dict[str, ExtractedPolicy] = {}
    parent_subs: Dict[str, List[ExtractedPolicy]] = {}

    for p in policies:
        if p.policy_type == "parent":
            parent_by_stmt[p.policy_statement] = p
            parent_subs[p.policy_statement] = []

    # Link subs to parents using the resolver's parent_policy_name
    orphan_subs: List[ExtractedPolicy] = []
    for p in policies:
        if p.policy_type != "sub":
            continue
        if p.parent_policy_name and p.parent_policy_name in parent_by_stmt:
            parent_subs[p.parent_policy_name].append(p)
        else:
            orphan_subs.append(p)

    # Build clusters with stable cluster_id
    clusters: List[dict] = []
    cid = 0

    # Parent clusters (with or without subs)
    for parent_stmt, parent in parent_by_stmt.items():
        subs = parent_subs[parent_stmt]
        header = _normalize_header(parent.section_header)
        clusters.append({
            "cluster_id": cid,
            "cluster_type": "parent_with_subs",
            "section_header": header,
            "parent": parent,
            "subs": subs,
        })
        cid += 1

    # Orphan subs (no matching parent in list)
    for sub in orphan_subs:
        header = _normalize_header(sub.section_header)
        clusters.append({
            "cluster_id": cid,
            "cluster_type": "orphan_sub",
            "section_header": header,
            "parent": None,
            "subs": [sub],
        })
        cid += 1

    # Individuals
    for p in policies:
        if p.policy_type == "individual":
            header = _normalize_header(p.section_header)
            clusters.append({
                "cluster_id": cid,
                "cluster_type": "individual",
                "section_header": header,
                "parent": None,
                "subs": [],
                "individual": p,
            })
            cid += 1

    return clusters


def summarize_clusters(clusters: List[dict]) -> None:
    parent_clusters = [c for c in clusters if c["cluster_type"] == "parent_with_subs"]
    individual_clusters = [c for c in clusters if c["cluster_type"] == "individual"]
    orphan_clusters = [c for c in clusters if c["cluster_type"] == "orphan_sub"]

    print(f"Total clusters:        {len(clusters)}")
    print(f"  Parent+sub clusters: {len(parent_clusters)}")
    print(f"  Individual clusters: {len(individual_clusters)}")
    print(f"  Orphaned subs:       {len(orphan_clusters)}\n")

    for i, c in enumerate(parent_clusters, 1):
        print(f"[Parent {i}] [{c['section_header']}]")
        print(f"  {c['parent'].policy_statement[:80]}")
        for j, sub in enumerate(c["subs"], 1):
            print(f"  └─ Sub {j}: {sub.policy_statement[:70]}")

    if individual_clusters:
        print(f"\n[Individuals] ({len(individual_clusters)} total)")
        for c in individual_clusters:
            print(f"  • [{c['section_header']}] {c['individual'].policy_statement[:75]}")

    if orphan_clusters:
        print(f"\n[Orphaned Subs] ({len(orphan_clusters)} total)")
        for c in orphan_clusters:
            print(f"  ? [{c['section_header']}] {c['subs'][0].policy_statement[:70]}")


def clusters_to_records(clusters: List[dict]) -> List[dict]:
    """
    Flatten clusters into a list of dict records, adding:
      - cluster_id
      - cluster_type
      - role (parent/sub/individual/orphan_sub)
      - parent_statement (for subs)
    """

    records: List[dict] = []

    for cluster_id, cluster in enumerate(clusters):
        ctype = cluster["cluster_type"]

        if ctype == "parent_with_subs":
            parent: ExtractedPolicy = cluster["parent"]
            records.append(
                {
                    **parent.model_dump(),
                    "cluster_id": cluster_id,
                    "cluster_type": ctype,
                    "role": "parent",
                    "parent_statement": None,
                }
            )
            for sub in cluster.get("subs", []):
                records.append(
                    {
                        **sub.model_dump(),
                        "cluster_id": cluster_id,
                        "cluster_type": ctype,
                        "role": "sub",
                        "parent_statement": parent.policy_statement,
                    }
                )

        elif ctype == "individual":
            policy: ExtractedPolicy = cluster["individual"]
            records.append(
                {
                    **policy.model_dump(),
                    "cluster_id": cluster_id,
                    "cluster_type": ctype,
                    "role": "individual",
                    "parent_statement": None,
                }
            )

        elif ctype == "orphan_sub":
            sub: ExtractedPolicy = cluster["subs"][0]
            records.append(
                {
                    **sub.model_dump(),
                    "cluster_id": cluster_id,
                    "cluster_type": ctype,
                    "role": "orphan_sub",
                    "parent_statement": sub.parent_policy_name,
                }
            )

    return records
