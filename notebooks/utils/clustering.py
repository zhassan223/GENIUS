from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from .schemas import ExtractedPolicy


def cluster_policies(policies: List[ExtractedPolicy]) -> List[dict]:
    """
    Cluster policies by `section_header`.

    Rules (same as notebook logic):
    - Parent + all subs with same section_header -> one 'parent_with_subs' cluster
    - Subs with no parent for that header -> one 'orphan_sub' cluster per sub
    - Individuals -> one 'individual' cluster per policy
    """

    by_header: Dict[str, dict] = defaultdict(
        lambda: {"parent": None, "subs": [], "individuals": []}
    )

    for policy in policies:
        header = policy.section_header.strip()
        if policy.policy_type == "parent":
            by_header[header]["parent"] = policy
        elif policy.policy_type == "sub":
            by_header[header]["subs"].append(policy)
        else:
            by_header[header]["individuals"].append(policy)

    clusters: List[dict] = []

    for header, group in by_header.items():
        if group["parent"] is not None:
            clusters.append(
                {
                    "cluster_type": "parent_with_subs",
                    "section_header": header,
                    "parent": group["parent"],
                    "subs": group["subs"],
                }
            )
        elif group["subs"]:
            for sub in group["subs"]:
                clusters.append(
                    {
                        "cluster_type": "orphan_sub",
                        "section_header": header,
                        "parent": None,
                        "subs": [sub],
                    }
                )

        for policy in group["individuals"]:
            clusters.append(
                {
                    "cluster_type": "individual",
                    "section_header": header,
                    "parent": None,
                    "subs": [],
                    "individual": policy,
                }
            )

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

