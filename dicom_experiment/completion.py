"""Explicit accounting of the manifest's intended comparisons."""

from .config import CONDITIONS


def intended_ids(manifest):
    entries = manifest.get("objects")
    if not isinstance(entries, list):
        raise ValueError("Manifest objects must be a list")
    ids = [entry.get("id") for entry in entries]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("Manifest instance identifiers must be nonempty and unique")
    for entry in entries:
        if entry.get("status") == "accepted" and not all(entry.get(k) for k in ("path", "sha256")):
            raise ValueError("Accepted manifest entry requires path and source hash")
    return {entry["id"] for entry in entries if entry.get("status") == "accepted"}


def successful_ids(run):
    success = set()
    for exported in run.exports:
        name = exported["instance"]
        rows = [row for row in run.measurements if row["instance"] == name]
        if len(rows) == len(CONDITIONS) and {r["condition"] for r in rows} == set(CONDITIONS):
            if all(row["png_matches_transform"] for row in rows):
                success.add(name)
    return success


def completion_status(intended, successful):
    if not intended:
        return "empty"
    if not successful:
        return "failed"
    return "complete" if successful == intended else "partial"


def completion_fields(manifest, run):
    intended = intended_ids(manifest)
    successful = successful_ids(run) - {failure["instance"] for failure in run.failures}
    status = completion_status(intended, successful)
    if status == "complete" and not comparison_passed(manifest, run):
        status = "partial"
    return {
        "intended_instances": len(intended),
        "successful_instances": len(successful),
        "failed_instances": len(intended - successful),
        "completion_status": status,
        "instance_outcomes": instance_outcomes(intended, successful),
    }


def instance_outcomes(intended, successful):
    return [
        {"instance": name, "status": "success" if name in successful else "failure"}
        for name in sorted(intended)
    ]


def comparison_passed(manifest, run):
    intended, successful = intended_ids(manifest), successful_ids(run)
    names = [entry["instance"] for entry in run.exports]
    return (
        bool(intended)
        and not run.failures
        and successful == intended
        and len(names) == len(set(names)) == len(intended)
        and len(run.measurements) == len(CONDITIONS) * len(intended)
    )
