"""Aggregate result construction and serialization; no acquisition dependencies."""

import json
from collections import Counter

from .artifacts import (
    artifact_hashes,
    dump,
    implementation_hashes,
    sha,
    versions,
    write_csv,
)
from .completion import comparison_passed, completion_fields, successful_ids
from .config import (
    CHECKSUMS_FILE,
    CONDITIONS,
    COUNTER_FIELDS,
    DATASET_NAME,
    EXPORT_MANIFEST_FILE,
    S3_ROOT,
    SUMMARY_FILE,
)
from .structure import chronology, group_series, uid

LIMITATIONS = [
    "One public individual; convenience sample, not cohort evidence.",
    "Metadata retention is not clinical information retention.",
    "PNG-only recovery excludes filenames, sidecars and OCR.",
    "Original source bytes must be retained; JSON sidecars omit binary values.",
    "Spatial and temporal conclusions apply only to the sample.",
]
REPORT_COMMANDS = "Run `python study_compare.py compare --manifest <manifest.json> --out <new-results>` then `python study_compare.py verify --manifest <manifest.json> --results <new-results>`."


def successful_items(run):
    successful = successful_ids(run)
    return [(name, ds) for name, ds in run.items if name in successful]


def coverage(run):
    items = successful_items(run)
    dates = {chronology(ds)["date"] for _, ds in items if chronology(ds)["valid_date"]}
    return {
        "modality_coverage": sorted({str(ds.get("Modality", "")) for _, ds in items}),
        "distinct_recorded_dates": len(dates),
    }


def real_successes(manifest, run):
    successful = successful_ids(run)
    if manifest.get("dataset") != DATASET_NAME:
        return set()
    return {
        e["id"]
        for e in manifest["objects"]
        if e["id"] in successful
        and e.get("url", "").startswith(S3_ROOT + "dicom/")
        and e.get("retrieved_at")
    }


def public_source_evidence(manifest, run):
    successful = successful_ids(run)
    return bool(successful) and real_successes(manifest, run) == successful


def cross_modality_coverage(run, covered):
    return (
        {"CT", "MR", "PT", "DX"}.issubset(covered["modality_coverage"])
        and covered["distinct_recorded_dates"] >= 2
        and has_distinct_series_sample(run)
    )


def acceptance_gates(manifest, run, covered):
    passed = comparison_passed(manifest, run)
    public = bool(public_source_evidence(manifest, run))
    return {
        "comparison_passed": passed,
        "public_source_provenance_recorded": public,
        "first_milestone": bool(real_successes(manifest, run)),
        "cross_modality_complete": passed and public and cross_modality_coverage(run, covered),
    }


def inventory_counts(rows):
    return {
        "inventory_presence_counts": dict(Counter(r["presence"] for r in rows)),
        "omitted_binary_elements": sum("binary_value_omitted" in r["validation"] for r in rows),
        "validation_findings": dict(Counter(v for r in rows for v in r["validation"])),
    }


def condition_totals(measurements):
    totals = {}
    for condition in CONDITIONS:
        group = [r for r in measurements if r["condition"] == condition]
        totals[condition] = {key: sum(r[key] for r in group) for key in COUNTER_FIELDS}
    return totals


def run_provenance(manifest_path):
    return {
        "schema_version": 1,
        "manifest_sha256": sha(manifest_path),
        "versions": versions(),
        "implementation_sha256": implementation_hashes(),
    }


def summary(manifest_path, manifest, run, tree):
    covered = coverage(run)
    return {
        **run_provenance(manifest_path),
        "source_objects": len(manifest["objects"]),
        "compared_instances": len(run.exports),
        **covered,
        "failures": run.failures,
        "excluded_objects": sum(e.get("status") != "accepted" for e in manifest["objects"]),
        "hierarchy_conflicts": len(tree["conflicts"]),
        **inventory_counts(run.inventory),
        **acceptance_gates(manifest, run, covered),
        "limitations": LIMITATIONS.copy(),
        **experiment_totals(manifest, run),
    }


def write_report(out, result):
    report = [
        "# Local DICOM comparison (pending human review)",
        "",
        REPORT_COMMANDS,
        "",
        "```json",
        json.dumps(result, indent=2),
        "```",
        "",
        "Detailed inventory, hierarchy, sources and PNGs are local patient-level artifacts; do not upload them.",
    ]
    (out / "report.md").write_text("\n".join(report) + "\n")


def write_results(out, manifest_path, run, tree, result):
    write_csv(out / "inventory.csv", run.inventory)
    write_csv(out / "comparison.csv", run.measurements)
    dump(out / "hierarchy.json", tree)
    dump(
        out / EXPORT_MANIFEST_FILE, {"manifest_sha256": sha(manifest_path), "objects": run.exports}
    )
    dump(out / SUMMARY_FILE, result)
    write_report(out, result)
    dump(out / CHECKSUMS_FILE, artifact_hashes(out))


def experiment_totals(manifest, run):
    return {**condition_totals(run.measurements), **completion_fields(manifest, run)}


def has_distinct_series_sample(run):
    for group in group_series(successful_items(run)).values():
        parents = {uid(ds, "StudyInstanceUID") for _, ds in group}
        instances = {uid(ds, "SOPInstanceUID") for _, ds in group}
        if (
            len(parents) == 1
            and None not in parents
            and None not in instances
            and len(instances) >= 2
        ):
            return True
    return False
