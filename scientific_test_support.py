"""Synthetic fixtures and coordinated report mutations for scientific-verification tests."""

import json

import pydicom
from pydicom.dataset import Dataset, FileMetaDataset

from comparison_test_support import fixture, source_entry
from dicom_experiment.artifacts import artifact_hashes, dump, load, sha, write_csv
from dicom_experiment.audit_records import read_table
from dicom_experiment.config import DATASET_NAME, S3_ROOT
from dicom_experiment.exporting import InspectorExporter
from dicom_experiment.metadata import retention as ORIGINAL_RETENTION
from dicom_experiment.pixels import pixel_metrics as ORIGINAL_PIXEL_METRICS


class SelectiveExporter:
    def __init__(self, failures):
        self.failures = set(failures)

    def export(self, source, destination):
        if destination.name in self.failures:
            raise ValueError("Injected synthetic export failure")
        return InspectorExporter().export(source, destination)


def synthetic_entry(root, index, public):
    folder = root / str(index)
    folder.mkdir()
    source, _, _ = fixture(folder)
    entry = {
        **source_entry(source),
        "id": f"instance-{index:03d}",
        "path": str(source.relative_to(root)),
    }
    if public:
        entry.update(url=S3_ROOT + "dicom/synthetic-test-only", retrieved_at="test-only")
    return entry


def multi_manifest(root, count=2, public=False):
    path = root / "manifest.json"
    entries = [synthetic_entry(root, index, public) for index in range(count)]
    dump(path, {"objects": entries, **({"dataset": DATASET_NAME} if public else {})})
    return path


def cross_modality_manifest(root):
    manifest = multi_manifest(root, 6, public=True)
    data = load(manifest)
    first = pydicom.dcmread(root / data["objects"][0]["path"])
    for index, entry in enumerate(data["objects"]):
        assign_modality(root, entry, index, first)
    dump(manifest, data)
    return manifest


def assign_modality(root, entry, index, first):
    ds = pydicom.dcmread(root / entry["path"])
    ds.Modality = ["CT", "CT", "MR", "PT", "DX", "CT"][index]
    ds.StudyDate = "20220102" if index == 2 else "20220101"
    if index == 1:
        ds.SeriesInstanceUID, ds.StudyInstanceUID = first.SeriesInstanceUID, first.StudyInstanceUID
    ds.save_as(root / entry["path"], enforce_file_format=True)
    entry["sha256"] = sha(root / entry["path"])


def refresh_checksums(out):
    dump(out / "checksums.json", artifact_hashes(out))


def synchronize_report(out, summary):
    report = (out / "report.md").read_text()
    before, remainder = report.split("```json\n", 1)
    _, after = remainder.split("```", 1)
    (out / "report.md").write_text(
        before + "```json\n" + json.dumps(summary, indent=2) + "\n```" + after
    )
    dump(out / "summary.json", summary)
    refresh_checksums(out)


def change_summary(out, key, value):
    summary = load(out / "summary.json")
    summary[key] = value
    synchronize_report(out, summary)


def change_measurement(out, field, value, condition="png_sidecar"):
    rows = read_table(out / "comparison.csv")
    row = next(row for row in rows if row["condition"] == condition)
    row[field] = value
    write_csv(out / "comparison.csv", rows)
    refresh_checksums(out)


def alter_counts_together(out):
    rows = read_table(out / "comparison.csv")
    row = next(row for row in rows if row["condition"] == "png_sidecar")
    row["all_matched"] = int(row["all_matched"]) - 1
    row["all_fraction"] = row["all_matched"] / int(row["all_eligible"])
    write_csv(out / "comparison.csv", rows)
    summary = load(out / "summary.json")
    summary["png_sidecar"]["all_matched"] -= 1
    synchronize_report(out, summary)


def small_metadata():
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.Rows, ds.Columns, ds.Manufacturer = 2, 2, ""
    ds.add_new(0x00111010, "LO", "private")
    ds.add_new(0x00111011, "OB", b"binary")
    child = Dataset()
    child.PixelSpacing = [1, 2]
    ds.SharedFunctionalGroupsSequence = [child]
    return ds


def expected_tiny_counts(matched, focused_matched):
    return {
        "all_eligible": 4,
        "all_matched": matched,
        "all_fraction": matched / 4,
        "focused_eligible": 3,
        "focused_matched": focused_matched,
        "focused_fraction": focused_matched / 3,
    }


def wrong_count_calculator(rows, recovered):
    result = ORIGINAL_RETENTION(rows, recovered)
    result["all_matched"] += 1
    result["all_fraction"] = result["all_matched"] / result["all_eligible"]
    return result


def wrong_pixel_calculator(pixels, png, transform):
    result = ORIGINAL_PIXEL_METRICS(pixels, png, transform)
    result["source_unique_values"] += 1
    return result
