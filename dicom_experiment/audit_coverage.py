"""Independent instance accounting and evidence-derived publication gate checks."""

from datetime import datetime

from .audit_records import check_fields, indexed
from .config import DATASET_NAME, S3_ROOT

OUTCOME_FIELDS = {
    "intended_instances",
    "successful_instances",
    "failed_instances",
    "completion_status",
    "instance_outcomes",
}


def accepted_entries(manifest):
    objects = indexed(manifest["objects"], ("id",))
    return {key[0]: entry for key, entry in objects.items() if entry.get("status") == "accepted"}


def reported_partition(manifest, export, summary):
    accepted = accepted_entries(manifest)
    successes = {key[0] for key in indexed(export["objects"], ("instance",))}
    failures = {key[0] for key in indexed(summary["failures"], ("instance",))}
    if successes & failures or successes | failures != accepted.keys():
        raise ValueError("Scientific audit: accepted instances are not fully partitioned")
    if any(
        not isinstance(f.get("error"), str) or not f["error"].strip() for f in summary["failures"]
    ):
        raise ValueError("Scientific audit: missing failure explanation")
    return accepted, successes, failures


def expected_completion(accepted, successes, failures):
    status = "empty" if not accepted else "failed"
    if successes:
        status = "partial" if failures else "complete"
    return {
        "intended_instances": len(accepted),
        "successful_instances": len(successes),
        "failed_instances": len(failures),
        "completion_status": status,
        "instance_outcomes": [
            {"instance": name, "status": "success" if name in successes else "failure"}
            for name in sorted(accepted)
        ],
    }


def check_completion(summary, expected):
    present = OUTCOME_FIELDS.intersection(summary)
    if present and present != OUTCOME_FIELDS:
        raise ValueError("Scientific audit: incomplete outcome schema")
    if present:
        indexed(summary["instance_outcomes"], ("instance",))
        check_fields(summary, expected, "completion")


def recorded_date(dataset):
    value = str(dataset.get("StudyDate", ""))
    try:
        datetime.strptime(value, "%Y%m%d")
        return value if len(value) == 8 else None
    except ValueError:
        return None


def evidence_coverage(datasets, successes):
    selected = [datasets[name] for name in successes]
    dates = {recorded_date(ds) for ds in selected} - {None}
    modalities = sorted({str(ds.get("Modality", "")) for ds in selected})
    return {
        "compared_instances": len(successes),
        "modality_coverage": modalities,
        "distinct_recorded_dates": len(dates),
    }


def genuine_successes(manifest, successes):
    if manifest.get("dataset") != DATASET_NAME:
        return set()
    return {
        entry["id"]
        for entry in manifest["objects"]
        if entry["id"] in successes
        and entry.get("url", "").startswith(S3_ROOT + "dicom/")
        and entry.get("retrieved_at")
    }


def evidence_gates(manifest, datasets, successes, completion, coverage):
    genuine = genuine_successes(manifest, successes)
    public = bool(successes) and genuine == successes
    complete = completion["completion_status"] == "complete"
    cross = complete and public and sampled_cross_modality(datasets, successes, coverage)
    return {
        "comparison_passed": complete,
        "public_source_provenance_recorded": public,
        "first_milestone": bool(genuine),
        "cross_modality_complete": cross,
    }


def distinct_series_sample(datasets, successes):
    selected = {name: datasets[name] for name in successes}
    for group in uid_groups(selected, "SeriesInstanceUID").values():
        studies = {str(ds.get("StudyInstanceUID", "")).strip() for ds in group}
        sops = {str(ds.get("SOPInstanceUID", "")).strip() for ds in group}
        if "" not in studies and "" not in sops and len(studies) == 1 and len(sops) >= 2:
            return True
    return False


def sampled_cross_modality(datasets, successes, coverage):
    return (
        {"CT", "MR", "PT", "DX"} <= set(coverage["modality_coverage"])
        and coverage["distinct_recorded_dates"] >= 2
        and distinct_series_sample(datasets, successes)
    )


def uid_groups(datasets, keyword):
    groups = {}
    for dataset in datasets.values():
        value = str(dataset.get(keyword, "")).strip()
        if value:
            groups.setdefault(value, []).append(dataset)
    return groups


def study_time_conflict(group):
    signatures = {
        (
            str(ds.get("StudyDate", "")),
            str(ds.get("StudyTime", "")),
            str(ds.get("TimezoneOffsetFromUTC", "")),
        )
        for ds in group
    }
    return len(signatures) != 1 or any(recorded_date(ds) is None for ds in group)


def missing_linkage_count(datasets):
    keys = ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID")
    return sum(any(not str(ds.get(key, "")).strip() for key in keys) for ds in datasets.values())


def hierarchy_conflict_count(datasets):
    sops = uid_groups(datasets, "SOPInstanceUID")
    series = uid_groups(datasets, "SeriesInstanceUID")
    studies = uid_groups(datasets, "StudyInstanceUID")
    duplicates = sum(len(group) > 1 for group in sops.values())
    parents = sum(
        len({str(ds.get("StudyInstanceUID", "")).strip() for ds in group}) > 1
        for group in series.values()
    )
    return (
        missing_linkage_count(datasets)
        + duplicates
        + parents
        + sum(study_time_conflict(group) for group in studies.values())
    )
