"""Study linkage and precision-aware spatial/temporal analysis, without I/O."""

import re
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np


def uid(ds, key):
    return str(ds.get(key, "")).strip() or None


def chronology(ds):
    date, time = str(ds.get("StudyDate", "")), str(ds.get("StudyTime", ""))
    offset = str(ds.get("TimezoneOffsetFromUTC", ""))
    findings = []
    try:
        datetime.strptime(date, "%Y%m%d")
        valid = len(date) == 8
    except ValueError:
        valid = False
    if not valid:
        findings.append("missing_or_invalid_study_date")
    if time and not re.fullmatch(
        r"(?:[01]\d|2[0-3])(?:[0-5]\d(?:[0-5]\d(?:\.\d{1,6})?)?)?", time
    ):
        findings.append("invalid_study_time")
    if offset and not re.fullmatch(r"[+-](?:0\d|1[0-4])[0-5]\d", offset):
        findings.append("invalid_timezone_offset")
    if not offset:
        findings.append("timezone_unknown")
    adt = str(ds.get("AcquisitionDateTime", ""))
    ad = str(ds.get("AcquisitionDate", ""))
    if date and (adt[:8] or ad) and date != (adt[:8] or ad):
        findings.append("study_acquisition_date_difference")
    return {
        "date": date or None,
        "time": time or None,
        "timezone": offset or None,
        "precision": (
            "fraction"
            if "." in time
            else {0: "day", 2: "hour", 4: "minute", 6: "second"}.get(
                len(time), "invalid"
            )
        ),
        "acquisition_datetime": adt or None,
        "acquisition_date": ad or None,
        "acquisition_time": str(ds.get("AcquisitionTime", "")) or None,
        "valid_date": valid,
        "findings": findings,
    }


def temporal_interval(record):
    """Precision-aware half-open interval; unknown offsets stay on a local clock."""
    if not record["valid_date"] or any(
        f in record["findings"]
        for f in ("invalid_study_time", "invalid_timezone_offset")
    ):
        return None
    date, time = record["date"], record["time"] or ""
    whole = time.split(".")[0]
    lower = datetime.strptime(date + whole.ljust(6, "0"), "%Y%m%d%H%M%S")
    if "." in time:
        fraction = time.split(".")[1]
        lower += timedelta(microseconds=int(fraction.ljust(6, "0")))
        width = timedelta(microseconds=10 ** (6 - len(fraction)))
    else:
        width = {
            0: timedelta(days=1),
            2: timedelta(hours=1),
            4: timedelta(minutes=1),
            6: timedelta(seconds=1),
        }[len(whole)]
    offset = record["timezone"]
    basis = "UTC" if offset else "recorded_local_clock_timezone_unknown"
    if offset:
        minutes = (int(offset[1:3]) * 60 + int(offset[3:])) * (
            -1 if offset[0] == "-" else 1
        )
        if not -720 <= minutes <= 840:
            return None
        lower -= timedelta(minutes=minutes)
    return basis, lower, lower + width


def order_exams(exams):
    clocks, unresolved = defaultdict(list), []
    for exam in exams:
        interval = temporal_interval(exam["temporal"]) if exam["consistent"] else None
        if interval is None:
            unresolved.append(exam["study"])
        else:
            basis, lo, hi = interval
            clocks[basis].append((lo, hi, exam["study"]))
    orders = {}
    for basis, intervals in clocks.items():
        groups = []
        for lo, hi, study in sorted(intervals):
            if groups and lo < groups[-1]["end"]:
                groups[-1]["studies"].append(study)
                groups[-1]["end"] = max(groups[-1]["end"], hi)
            else:
                groups.append({"start": lo, "end": hi, "studies": [study]})
        orders[basis] = [
            {
                "start": g["start"].isoformat(),
                "end_exclusive": g["end"].isoformat(),
                "studies": g["studies"],
                "tie_unresolved": len(g["studies"]) > 1,
            }
            for g in groups
        ]
    return {
        "basis": "StudyDate + StudyTime precision intervals; known offsets converted to UTC; clocks with unknown offsets are not compared to UTC",
        "ordered_groups_by_clock": orders,
        "unresolved_studies": unresolved,
    }


def spatial_order(items):
    if len(items) < 2:
        return {"status": "insufficient_sample", "order": [], "sample_gaps_mm": []}
    try:
        if any(int(ds.get("NumberOfFrames", 1)) != 1 for _, ds in items):
            raise ValueError("multiframe_geometry_not_resolved")
        refs = {uid(ds, "FrameOfReferenceUID") for _, ds in items}
        if None in refs or len(refs) != 1:
            raise ValueError("missing_or_incompatible_frame_of_reference")
        orientations = [
            np.asarray(ds.ImageOrientationPatient, dtype=float) for _, ds in items
        ]
        ori = orientations[0]
        if ori.shape != (6,) or not np.isfinite(ori).all():
            raise ValueError("invalid_orientation")
        if not (
            np.isclose(np.linalg.norm(ori[:3]), 1, atol=1e-4)
            and np.isclose(np.linalg.norm(ori[3:]), 1, atol=1e-4)
            and np.isclose(np.dot(ori[:3], ori[3:]), 0, atol=1e-4)
        ):
            raise ValueError("invalid_direction_cosines")
        if any(
            o.shape != (6,) or not np.allclose(o, ori, atol=1e-4, rtol=0)
            for o in orientations
        ):
            raise ValueError("incompatible_orientation")
        normal = np.cross(ori[:3], ori[3:])
        projected = []
        for name, ds in items:
            pos = np.asarray(ds.ImagePositionPatient, dtype=float)
            if pos.shape != (3,) or not np.isfinite(pos).all():
                raise ValueError("invalid_position")
            projected.append((float(np.dot(pos, normal)), name))
        projected.sort()
        gaps = np.diff([p[0] for p in projected]).tolist()
        return {
            "status": "ambiguous_duplicate_positions"
            if any(abs(g) < 1e-6 for g in gaps)
            else "ordered",
            "order": [p[1] for p in projected],
            "sample_gaps_mm": gaps,
            "scope": "sample only; gaps do not establish missing slices or full-series spacing",
        }
    except (AttributeError, TypeError, ValueError) as exc:
        return {
            "status": "unresolved",
            "reason": str(exc),
            "order": [],
            "sample_gaps_mm": [],
        }


def hierarchy(items):
    studies, series, sops = defaultdict(list), defaultdict(list), defaultdict(list)
    nodes, conflicts = [], []
    for name, ds in items:
        node = {
            "instance": name,
            **{
                k: uid(ds, k)
                for k in ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID")
            },
            "temporal": chronology(ds),
        }
        nodes.append(node)
        study, seri, sop = [
            node[k] for k in ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID")
        ]
        if study:
            studies[study].append(node)
        if seri:
            series[seri].append((name, ds))
        if sop:
            sops[sop].append(node)
        if not all((study, seri, sop)):
            conflicts.append({"instance": name, "finding": "missing_uid_linkage"})
    for sop, group in sops.items():
        if len(group) > 1:
            conflict = (
                len({(n["StudyInstanceUID"], n["SeriesInstanceUID"]) for n in group})
                > 1
            )
            conflicts.append(
                {
                    "sop": sop,
                    "finding": "conflicting_sop_linkage"
                    if conflict
                    else "duplicate_sop_uid",
                }
            )
    for seri, group in series.items():
        if len({uid(ds, "StudyInstanceUID") for _, ds in group}) > 1:
            conflicts.append(
                {"series": seri, "finding": "conflicting_series_study_linkage"}
            )
    exams = []
    for study, group in studies.items():
        times = [n["temporal"] for n in group]
        signatures = {(t["date"], t["time"], t["timezone"]) for t in times}
        consistent = len(signatures) == 1 and all(t["valid_date"] for t in times)
        if not consistent:
            conflicts.append(
                {"study": study, "finding": "inconsistent_or_invalid_study_time"}
            )
        exams.append(
            {
                "study": study,
                "date": times[0]["date"] if consistent else None,
                "temporal": times[0],
                "consistent": consistent,
            }
        )
    return {
        "instances": nodes,
        "studies": dict(studies),
        "conflicts": conflicts,
        "spatial": {key: spatial_order(group) for key, group in series.items()},
        "examinations": exams,
        "chronology": order_exams(exams),
    }
