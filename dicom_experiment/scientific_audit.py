"""Reconcile scientific claims against evidence, independently of their production code."""

from collections import Counter
from dataclasses import dataclass, field

import pydicom

from .artifacts import inside, load
from .audit_coverage import (
    check_completion,
    evidence_coverage,
    evidence_gates,
    expected_completion,
    hierarchy_conflict_count,
    reported_partition,
)
from .audit_metadata import (
    count_retention,
    eligible_paths,
    exact_paths,
    inventory_expectations,
    recovery_flags,
    representation_values,
    source_inventory,
)
from .audit_pixels import load_png_pixels, pixel_evidence
from .audit_records import check_fields, check_report, check_table, read_table
from .config import CONDITIONS, METADATA_FILE, SUMMARY_FILE


@dataclass
class ScientificEvidence:
    datasets: dict = field(default_factory=dict)
    inventory: list = field(default_factory=list)
    comparisons: list = field(default_factory=list)
    eligible: dict = field(default_factory=dict)
    matched: dict = field(default_factory=dict)


def condition_record(name, ds, label, inventory, values, metrics):
    errors = metrics.copy()
    if label != "png_sidecar":
        errors.update(
            reconstruction_mae_stored_units=None, reconstruction_max_error_stored_units=None
        )
    return {
        "instance": name,
        "modality": str(ds.get("Modality", "")),
        "condition": label,
        **count_retention(inventory, values),
        **errors,
        **recovery_flags(inventory, values),
    }


def accumulate_observations(evidence, name, rows, representations):
    for condition, values in representations.items():
        matches = exact_paths(rows, values)
        for group, focused in (("all", False), ("focused", True)):
            key = (condition, group)
            selected = eligible_paths(rows, focused)
            evidence.eligible.setdefault(key, set()).update((name, path) for path in selected)
            evidence.matched.setdefault(key, set()).update(
                (name, path) for path in selected & matches
            )


def audit_export(context, entry, obj, ds, rows, evidence):
    png = load_png_pixels(context.results, obj["frames"])
    metrics = pixel_evidence(ds, context.decoder.decode(ds), png)
    if not metrics["png_matches_transform"]:
        raise ValueError("Scientific audit: source-to-PNG transformation mismatch")
    side = load(inside(context.results, entry["id"]) / METADATA_FILE)
    representations = representation_values(rows, png, side, str(ds.file_meta.TransferSyntaxUID))
    for label, values in representations.items():
        evidence.comparisons.append(condition_record(entry["id"], ds, label, rows, values, metrics))
    evidence.inventory.extend(inventory_expectations(entry["id"], rows, representations))
    accumulate_observations(evidence, entry["id"], rows, representations)


def gather_evidence(context, accepted, export):
    evidence = ScientificEvidence()
    exports = {obj["instance"]: obj for obj in export["objects"]}
    for name, entry in accepted.items():
        ds = pydicom.dcmread(inside(context.root, entry["path"]))
        evidence.datasets[name] = ds
        rows = source_inventory(ds)
        if name in exports:
            audit_export(context, entry, exports[name], ds, rows, evidence)
        else:
            evidence.inventory.extend(inventory_expectations(name, rows))
    return evidence


def aggregate_observations(evidence, condition):
    result = {}
    for group in ("all", "focused"):
        key = (condition, group)
        result[group + "_eligible"] = len(evidence.eligible.get(key, set()))
        result[group + "_matched"] = len(evidence.matched.get(key, set()))
    return result


def inventory_totals(evidence):
    presence, validation = Counter(), Counter()
    for row in evidence.inventory:
        presence[row["presence"]] += 1
        validation.update(row["validation"])
    return {
        "inventory_presence_counts": dict(presence),
        "validation_findings": dict(validation),
        "omitted_binary_elements": validation.get("binary_value_omitted", 0),
    }


def check_scientific_tables(context, evidence):
    comparisons = read_table(context.results / "comparison.csv")
    inventory = [
        optional_match_columns(row) for row in read_table(context.results / "inventory.csv")
    ]
    check_table(comparisons, evidence.comparisons, ("instance", "condition"), "comparison")
    check_table(inventory, evidence.inventory, ("instance", "path"), "inventory")


def check_summary(context, summary, evidence, successes, completion):
    coverage = evidence_coverage(evidence.datasets, successes)
    gates = evidence_gates(context.manifest, evidence.datasets, successes, completion, coverage)
    accepted = sum(e.get("status") == "accepted" for e in context.manifest["objects"])
    counts = {
        "source_objects": len(context.manifest["objects"]),
        "hierarchy_conflicts": hierarchy_conflict_count(evidence.datasets),
        "excluded_objects": len(context.manifest["objects"]) - accepted,
    }
    check_fields(summary, {**counts, **coverage, **gates, **inventory_totals(evidence)}, "summary")
    for condition in CONDITIONS:
        check_aggregate(summary[condition], evidence, condition)
    check_completion(summary, completion)
    check_report(context.results / "report.md", summary)


def audit_scientific_outputs(context, export):
    summary = load(context.results / SUMMARY_FILE)
    accepted, successes, failures = reported_partition(context.manifest, export, summary)
    completion = expected_completion(accepted, successes, failures)
    evidence = gather_evidence(context, accepted, export)
    check_scientific_tables(context, evidence)
    check_summary(context, summary, evidence, successes, completion)
    return {
        "scientific_verification_passed": True,
        "completion_status": completion["completion_status"],
        "verified_scientific_instances": len(successes),
        "reported_failed_instances": len(failures),
    }


def check_aggregate(actual, evidence, condition):
    expected = aggregate_observations(evidence, condition)
    for group in ("all", "focused"):
        if group + "_fraction" in actual:
            denominator = expected[group + "_eligible"]
            expected[group + "_fraction"] = (
                expected[group + "_matched"] / denominator if denominator else None
            )
    check_fields(actual, expected, condition)


def optional_match_columns(row):
    """Older all-failed inventories omit match columns rather than claiming recovery."""
    return {"png_only_exact": "", "png_sidecar_exact": "", **row}
