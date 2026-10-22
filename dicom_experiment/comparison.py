"""Offline comparison use case; coordinates pure measurements and artifact adapters."""

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pydicom
from pydicom.datadict import tag_for_keyword

from inspector import inspect

from .artifacts import (
    artifact_hashes,
    dump,
    implementation_hashes,
    inside,
    load,
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
    METADATA_FILE,
    RECOVERY_FIELDS,
    S3_ROOT,
    SUMMARY_FILE,
)
from .metadata import flatten_json, inventory, retention
from .pixels import (
    DisplayTransform,
    frame_array,
    inspector_input,
    pixel_metrics,
    read_pngs,
)
from .structure import chronology, hierarchy, uid


@dataclass
class InstanceComparison:
    measurements: list[dict]
    export: dict


def compare_instance(
    entry: dict,
    source: Path,
    ds,
    rows: list[dict],
    instance_inventory: list[dict],
    out: Path,
    decoder: str,
) -> InstanceComparison:
    name = entry["id"]
    dest = inside(out, name)
    inspected_source = inspector_input(source, ds, dest, decoder)
    result = inspect(inspected_source, dest)
    paths = sorted(dest.glob("frame-*.png"))
    png = read_pngs(paths)
    pixels = frame_array(ds, decoder)
    transform = DisplayTransform.from_pixels(pixels, str(ds.PhotometricInterpretation))
    metrics = pixel_metrics(pixels, png["pixels"], transform)
    export = {
        "instance": name,
        "source_sha256": entry["sha256"],
        "inspector_input_sha256": sha(inspected_source),
        "derived_native_path": str(inspected_source.relative_to(out))
        if inspected_source != source
        else None,
        "original_transfer_syntax": str(ds.file_meta.TransferSyntaxUID),
        "decoder": decoder,
        "sidecar_sha256": sha(dest / METADATA_FILE),
        "low": transform.low,
        "high": transform.high,
        "inverted": transform.inverted,
        "transform": result["display"]["transform"],
        "frames": [
            {"index": i, "path": str(p.relative_to(out)), "sha256": sha(p)}
            for i, p in enumerate(paths)
        ],
    }
    sidecar = load(dest / METADATA_FILE)
    side_rows = flatten_json(sidecar["raw_inventory"])
    recovered = {
        r["path"]: r["value"]
        for r in side_rows
        if r["countable"] and r["presence"] == "present"
    }
    # This original syntax is explicitly preserved in the added export manifest,
    # whereas the inspector sidecar describes its derived native input.
    recovered["file_meta.00020010"] = [export["original_transfer_syntax"]]
    native = {
        r["path"]: r["value"]
        for r in rows
        if r["countable"] and r["presence"] == "present"
    }
    # Only dimensions have direct, exact DICOM field equivalents in PNG containers.
    png_fields = {"00280010": [png["Rows"]], "00280011": [png["Columns"]]}
    for row in instance_inventory:
        eligible = row["countable"] and row["presence"] == "present"
        for label, recovered_fields in [
            ("png_only", png_fields),
            ("png_sidecar", recovered),
        ]:
            row[label + "_exact"] = (
                (
                    row["path"] in recovered_fields
                    and recovered_fields[row["path"]] == row["value"]
                )
                if eligible
                else None
            )
    instance_comparisons = []
    for condition, fields in [
        ("dicom", native),
        ("png_only", png_fields),
        ("png_sidecar", recovered),
    ]:
        rec = {
            "instance": name,
            "modality": str(ds.get("Modality", "")),
            "condition": condition,
            **retention(rows, fields),
            "decoded_frames": len(pixels),
            "export_frames": png["frame_count"],
            "source_bits_stored": int(ds.BitsStored),
            "export_bits": png["bits"],
            **metrics,
        }
        if condition != "png_sidecar":
            rec["reconstruction_mae_stored_units"] = None
            rec["reconstruction_max_error_stored_units"] = None
        for keyword in RECOVERY_FIELDS:
            path = f"{tag_for_keyword(keyword):08X}"
            rec[keyword + "_recoverable"] = (
                (path in fields and fields[path] == native[path])
                if path in native
                else None
            )
        instance_comparisons.append(rec)
    return InstanceComparison(instance_comparisons, export)


def compare(manifest_path: Path | str, out: Path | str) -> dict:
    manifest_path, out = Path(manifest_path).resolve(), Path(out).resolve()
    manifest = load(manifest_path)
    decoder = manifest.get("decoder", "native")
    if out.exists() and any(out.iterdir()):
        raise ValueError("Output must be empty; use a new directory for each run")
    out.mkdir(parents=True, exist_ok=True)
    inventory_rows, comparisons, items, failures, exports = [], [], [], [], []
    for entry in manifest["objects"]:
        if entry.get("status") != "accepted":
            continue
        name = entry["id"]
        source = inside(manifest_path.parent, entry["path"])
        if not source.is_file() or sha(source) != entry["sha256"]:
            raise ValueError(f"Source missing or hash mismatch: {name}")
        ds = pydicom.dcmread(source)
        rows = inventory(ds)
        instance_inventory = [{"instance": name, **r} for r in rows]
        inventory_rows.extend(instance_inventory)
        items.append((name, ds))
        try:
            result = compare_instance(
                entry, source, ds, rows, instance_inventory, out, decoder
            )
            comparisons.extend(result.measurements)
            exports.append(result.export)
        except Exception as exc:
            failures.append({"instance": name, "error": str(exc)})
    tree = hierarchy(items)
    successful = {e["instance"] for e in exports}
    modalities = sorted(
        {str(ds.get("Modality", "")) for name, ds in items if name in successful}
    )
    dates = {
        chronology(ds)["date"]
        for name, ds in items
        if name in successful and chronology(ds)["valid_date"]
    }
    series_counts = Counter(
        uid(ds, "SeriesInstanceUID") for name, ds in items if name in successful
    )
    comparison_passed = (
        bool(exports)
        and len(comparisons) == 3 * len(exports)
        and all(r["png_matches_transform"] for r in comparisons)
    )
    # Synthetic or user-created manifests exercise the pipeline, not the real-data gate.
    public_source_evidence = manifest.get("dataset") == DATASET_NAME and all(
        e.get("url", "").startswith(S3_ROOT + "dicom/") and e.get("retrieved_at")
        for e in manifest["objects"]
        if e["id"] in successful
    )
    summary = {
        "schema_version": 1,
        "manifest_sha256": sha(manifest_path),
        "versions": versions(),
        "implementation_sha256": implementation_hashes(),
        "source_objects": len(manifest["objects"]),
        "compared_instances": len(exports),
        "modality_coverage": modalities,
        "distinct_recorded_dates": len(dates),
        "failures": failures,
        "excluded_objects": sum(
            e.get("status") != "accepted" for e in manifest["objects"]
        ),
        "hierarchy_conflicts": len(tree["conflicts"]),
        "inventory_presence_counts": dict(
            Counter(r["presence"] for r in inventory_rows)
        ),
        "omitted_binary_elements": sum(
            "binary_value_omitted" in r["validation"] for r in inventory_rows
        ),
        "validation_findings": dict(
            Counter(v for r in inventory_rows for v in r["validation"])
        ),
        "comparison_passed": comparison_passed,
        "public_source_provenance_recorded": bool(public_source_evidence),
        "first_milestone": comparison_passed and bool(public_source_evidence),
        "cross_modality_complete": comparison_passed
        and bool(public_source_evidence)
        and set(("CT", "MR", "PT", "DX")).issubset(modalities)
        and len(dates) >= 2
        and any(k and n >= 2 for k, n in series_counts.items()),
        "limitations": [
            "One public individual; convenience sample, not cohort evidence.",
            "Metadata retention is not clinical information retention.",
            "PNG-only recovery excludes filenames, sidecars and OCR.",
            "Original source bytes must be retained; JSON sidecars omit binary values.",
            "Spatial and temporal conclusions apply only to the sample.",
        ],
    }
    for condition in CONDITIONS:
        group = [r for r in comparisons if r["condition"] == condition]
        summary[condition] = {k: sum(r[k] for r in group) for k in COUNTER_FIELDS}
    write_csv(out / "inventory.csv", inventory_rows)
    write_csv(out / "comparison.csv", comparisons)
    dump(out / "hierarchy.json", tree)
    dump(
        out / EXPORT_MANIFEST_FILE,
        {"manifest_sha256": sha(manifest_path), "objects": exports},
    )
    dump(out / SUMMARY_FILE, summary)
    report = [
        "# Local DICOM comparison (pending human review)",
        "",
        "Run `python study_compare.py compare --manifest <manifest.json> --out <new-results>` then `python study_compare.py verify --manifest <manifest.json> --results <new-results>`.",
        "",
        "```json",
        json.dumps(summary, indent=2),
        "```",
        "",
        "Detailed inventory, hierarchy, sources and PNGs are local patient-level artifacts; do not upload them.",
    ]
    (out / "report.md").write_text("\n".join(report) + "\n")
    # Bind every generated file to this run; verify detects missing/changed artifacts.
    dump(out / CHECKSUMS_FILE, artifact_hashes(out))
    return summary
