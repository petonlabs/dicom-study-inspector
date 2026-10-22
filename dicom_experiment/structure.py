"""UID linkage and hierarchy; temporal and spatial calculations have separate owners."""

from collections import defaultdict

from .spatial import spatial_order, uid
from .temporal import chronology, order_exams

__all__ = ["chronology", "hierarchy", "spatial_order", "uid"]
LINK_KEYS = ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID")


def instance_node(name, ds):
    return {
        "instance": name,
        **{key: uid(ds, key) for key in LINK_KEYS},
        "temporal": chronology(ds),
    }


def group_nodes(nodes, key):
    groups = defaultdict(list)
    for node in nodes:
        if node[key]:
            groups[node[key]].append(node)
    return dict(groups)


def group_series(items):
    groups = defaultdict(list)
    for name, ds in items:
        if uid(ds, "SeriesInstanceUID"):
            groups[uid(ds, "SeriesInstanceUID")].append((name, ds))
    return dict(groups)


def sop_conflicts(nodes):
    conflicts = []
    for sop, group in group_nodes(nodes, "SOPInstanceUID").items():
        if len(group) > 1:
            parents = {(n["StudyInstanceUID"], n["SeriesInstanceUID"]) for n in group}
            finding = "conflicting_sop_linkage" if len(parents) > 1 else "duplicate_sop_uid"
            conflicts.append({"sop": sop, "finding": finding})
    return conflicts


def linkage_conflicts(nodes, series):
    missing = [
        {"instance": n["instance"], "finding": "missing_uid_linkage"}
        for n in nodes
        if not all(n[k] for k in LINK_KEYS)
    ]
    conflicts = missing + sop_conflicts(nodes)
    for key, group in series.items():
        if len({uid(ds, "StudyInstanceUID") for _, ds in group}) > 1:
            conflicts.append({"series": key, "finding": "conflicting_series_study_linkage"})
    return conflicts


def examination(study, group):
    times = [node["temporal"] for node in group]
    signatures = {(t["date"], t["time"], t["timezone"]) for t in times}
    consistent = len(signatures) == 1 and all(t["valid_date"] for t in times)
    return {
        "study": study,
        "date": times[0]["date"] if consistent else None,
        "temporal": times[0],
        "consistent": consistent,
    }


def examination_conflicts(exams):
    return [
        {"study": e["study"], "finding": "inconsistent_or_invalid_study_time"}
        for e in exams
        if not e["consistent"]
    ]


def hierarchy(items):
    nodes = [instance_node(name, ds) for name, ds in items]
    studies, series = group_nodes(nodes, "StudyInstanceUID"), group_series(items)
    exams = [examination(study, group) for study, group in studies.items()]
    conflicts = linkage_conflicts(nodes, series) + examination_conflicts(exams)
    return {
        "instances": nodes,
        "studies": studies,
        "conflicts": conflicts,
        "spatial": {key: spatial_order(group) for key, group in series.items()},
        "examinations": exams,
        "chronology": order_exams(exams),
    }
