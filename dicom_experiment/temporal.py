"""Precision-aware examination timestamps and ordering; no I/O."""

import re
from collections import defaultdict
from datetime import datetime, timedelta

LOCAL_CLOCK = "recorded_local_clock_timezone_unknown"
TIME_PATTERN = r"(?:[01]\d|2[0-3])(?:[0-5]\d(?:[0-5]\d(?:\.\d{1,6})?)?)?"
OFFSET_PATTERN = r"[+-](?:0\d|1[0-4])[0-5]\d"
PRECISIONS = {0: "day", 2: "hour", 4: "minute", 6: "second"}
WIDTHS = {
    0: timedelta(days=1),
    2: timedelta(hours=1),
    4: timedelta(minutes=1),
    6: timedelta(seconds=1),
}
ORDER_BASIS = (
    "StudyDate + StudyTime precision intervals; known offsets converted to UTC; "
    "clocks with unknown offsets are not compared to UTC"
)


def valid_date(date):
    try:
        datetime.strptime(date, "%Y%m%d")
        return len(date) == 8
    except ValueError:
        return False


def temporal_findings(date, time, offset, acquisition_date):
    findings = []
    if not valid_date(date):
        findings.append("missing_or_invalid_study_date")
    if time and not re.fullmatch(TIME_PATTERN, time):
        findings.append("invalid_study_time")
    if offset and not re.fullmatch(OFFSET_PATTERN, offset):
        findings.append("invalid_timezone_offset")
    if not offset:
        findings.append("timezone_unknown")
    if date and acquisition_date and date != acquisition_date:
        findings.append("study_acquisition_date_difference")
    return findings


def chronology(ds):
    date, time = str(ds.get("StudyDate", "")), str(ds.get("StudyTime", ""))
    offset = str(ds.get("TimezoneOffsetFromUTC", ""))
    adt, ad = str(ds.get("AcquisitionDateTime", "")), str(ds.get("AcquisitionDate", ""))
    return {
        "date": date or None,
        "time": time or None,
        "timezone": offset or None,
        "precision": "fraction" if "." in time else PRECISIONS.get(len(time), "invalid"),
        "acquisition_datetime": adt or None,
        "acquisition_date": ad or None,
        "acquisition_time": str(ds.get("AcquisitionTime", "")) or None,
        "valid_date": valid_date(date),
        "findings": temporal_findings(date, time, offset, adt[:8] or ad),
    }


def local_interval(date, time):
    whole = time.split(".")[0]
    lower = datetime.strptime(date + whole.ljust(6, "0"), "%Y%m%d%H%M%S")
    if "." not in time:
        return lower, WIDTHS[len(whole)]
    fraction = time.split(".")[1]
    lower += timedelta(microseconds=int(fraction.ljust(6, "0")))
    return lower, timedelta(microseconds=10 ** (6 - len(fraction)))


def offset_minutes(offset):
    if not offset:
        return 0
    sign = -1 if offset[0] == "-" else 1
    return (int(offset[1:3]) * 60 + int(offset[3:])) * sign


def temporal_interval(record):
    """Half-open interval; unknown offsets remain on a separate local clock."""
    invalid = {"invalid_study_time", "invalid_timezone_offset"}
    if not record["valid_date"] or invalid.intersection(record["findings"]):
        return None
    minutes = offset_minutes(record["timezone"])
    if not -720 <= minutes <= 840:
        return None
    lower, width = local_interval(record["date"], record["time"] or "")
    lower -= timedelta(minutes=minutes)
    basis = "UTC" if record["timezone"] else LOCAL_CLOCK
    return basis, lower, lower + width


def group_intervals(intervals):
    groups = []
    for lo, hi, study in sorted(intervals):
        if groups and lo < groups[-1]["end"]:
            groups[-1]["studies"].append(study)
            groups[-1]["end"] = max(groups[-1]["end"], hi)
        else:
            groups.append({"start": lo, "end": hi, "studies": [study]})
    return [serialized_interval(group) for group in groups]


def serialized_interval(group):
    return {
        "start": group["start"].isoformat(),
        "end_exclusive": group["end"].isoformat(),
        "studies": group["studies"],
        "tie_unresolved": len(group["studies"]) > 1,
    }


def order_exams(exams):
    clocks, unresolved = defaultdict(list), []
    for exam in exams:
        interval = temporal_interval(exam["temporal"]) if exam["consistent"] else None
        if interval is None:
            unresolved.append(exam["study"])
        else:
            basis, lo, hi = interval
            clocks[basis].append((lo, hi, exam["study"]))
    orders = {basis: group_intervals(intervals) for basis, intervals in clocks.items()}
    return {
        "basis": ORDER_BASIS,
        "ordered_groups_by_clock": orders,
        "unresolved_studies": unresolved,
    }
