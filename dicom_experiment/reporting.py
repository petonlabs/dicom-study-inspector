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
from .config import (
    CHECKSUMS_FILE,
    CONDITIONS,
    COUNTER_FIELDS,
    DATASET_NAME,
    EXPORT_MANIFEST_FILE,
    S3_ROOT,
    SUMMARY_FILE,
)
from .structure import chronology, uid

LIMITATIONS = [
    "One public individual; convenience sample, not cohort evidence.",
    "Metadata retention is not clinical information retention.",
    "PNG-only recovery excludes filenames, sidecars and OCR.",
    "Original source bytes must be retained; JSON sidecars omit binary values.",
    "Spatial and temporal conclusions apply only to the sample.",
]
REPORT_COMMANDS = "Run `python study_compare.py compare --manifest <manifest.json> --out <new-results>` then `python study_compare.py verify --manifest <manifest.json> --results <new-results>`."


def successful_items(run):
    successful = {e["instance"] for e in run.exports}
    return [(name, ds) for name, ds in run.items if name in successful]


def coverage(run):
    items = successful_items(run)
    dates = {chronology(ds)["date"] for _, ds in items if chronology(ds)["valid_date"]}
    return {
        "modality_coverage": sorted({str(ds.get("Modality", "")) for _, ds in items}),
        "distinct_recorded_dates": len(dates),
    }


def public_source_evidence(manifest, run):
    successful = {e["instance"] for e in run.exports}
    return manifest.get("dataset") == DATASET_NAME and all(
        e.get("url", "").startswith(S3_ROOT + "dicom/") and e.get("retrieved_at")
        for e in manifest["objects"]
        if e["id"] in successful
    )


def comparison_passed(run):
    return (
        bool(run.exports)
        and len(run.measurements) == 3 * len(run.exports)
        and all(r["png_matches_transform"] for r in run.measurements)
    )


def cross_modality_coverage(run, covered):
    series = Counter(uid(ds, "SeriesInstanceUID") for _, ds in successful_items(run))
    return (
        {"CT", "MR", "PT", "DX"}.issubset(covered["modality_coverage"])
        and covered["distinct_recorded_dates"] >= 2
        and any(key and count >= 2 for key, count in series.items())
    )


def acceptance_gates(manifest, run, covered):
    passed = comparison_passed(run)
    public = bool(public_source_evidence(manifest, run))
    return {
        "comparison_passed": passed,
        "public_source_provenance_recorded": public,
        "first_milestone": passed and public,
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
        **condition_totals(run.measurements),
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
