"""Scientific claims must survive an independent evidence audit, not just checksums."""

import copy
from unittest.mock import patch

import numpy as np
import pydicom
import pytest
from pydicom.dataset import Dataset, FileMetaDataset

from comparison_test_support import compared_fixture, fixture
from dicom_experiment.artifacts import dump, load, sha, write_csv
from dicom_experiment.audit_coverage import OUTCOME_FIELDS
from dicom_experiment.audit_metadata import (
    count_retention,
    representation_values,
    source_inventory,
)
from dicom_experiment.audit_pixels import intensity_metrics
from dicom_experiment.audit_records import read_table
from dicom_experiment.comparison import compare
from dicom_experiment.completion import comparison_passed
from dicom_experiment.models import ComparisonRun
from dicom_experiment.verification import verify
from scientific_test_support import (
    SelectiveExporter,
    alter_counts_together,
    change_measurement,
    change_summary,
    cross_modality_manifest,
    expected_tiny_counts,
    multi_manifest,
    refresh_checksums,
    small_metadata,
    synchronize_report,
    wrong_count_calculator,
    wrong_pixel_calculator,
)


@pytest.mark.parametrize(
    "count,failures,status,successful",
    [
        (0, [], "empty", 0),
        (2, [], "complete", 2),
        (2, ["instance-001"], "partial", 1),
        (2, ["instance-000", "instance-001"], "failed", 0),
    ],
)
def test_completion_and_consistent_partial_verification(
    tmp_path, count, failures, status, successful
):
    manifest = multi_manifest(tmp_path, count)
    result = compare(manifest, tmp_path / "out", exporter=SelectiveExporter(failures))
    assert result["completion_status"] == status and result["successful_instances"] == successful
    assert result["intended_instances"] == count and result["failed_instances"] == len(failures)
    assert result["comparison_passed"] == (status == "complete")
    checked = verify(manifest, tmp_path / "out")
    assert checked["scientific_verification_passed"] and checked["completion_status"] == status


def test_real_provenance_first_milestone_survives_partial_run(tmp_path):
    manifest = multi_manifest(tmp_path, public=True)
    result = compare(manifest, tmp_path / "out", exporter=SelectiveExporter(["instance-001"]))
    assert result["first_milestone"] and not result["comparison_passed"]
    assert not result["cross_modality_complete"]
    assert verify(manifest, tmp_path / "out")["completion_status"] == "partial"


def test_missing_intended_outcome_prevents_completion():
    manifest = {"objects": [{"id": "missing", "path": "x", "sha256": "x", "status": "accepted"}]}
    assert not comparison_passed(manifest, ComparisonRun())


def test_duplicate_manifest_ids_are_fatal_before_output(tmp_path):
    manifest = multi_manifest(tmp_path)
    data = load(manifest)
    data["objects"][1]["id"] = data["objects"][0]["id"]
    dump(manifest, data)
    with pytest.raises(ValueError, match="unique"):
        compare(manifest, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_loading_failure_preserves_earlier_success_but_cannot_verify_missing_evidence(tmp_path):
    manifest = multi_manifest(tmp_path)
    (tmp_path / load(manifest)["objects"][1]["path"]).unlink()
    result = compare(manifest, tmp_path / "out")
    assert result["completion_status"] == "partial" and len(result["failures"]) == 1
    assert result["compared_instances"] == 1
    with pytest.raises(ValueError, match="Source missing"):
        verify(manifest, tmp_path / "out")


def test_hand_counted_metadata_denominators_and_missing_binary_exclusions():
    ds = small_metadata()
    rows = source_inventory(ds)
    representations = representation_values(
        rows, np.zeros((1, 2, 2), dtype=np.uint8), {"raw_inventory": {}}, "syntax"
    )
    assert count_retention(rows, representations["dicom"]) == expected_tiny_counts(4, 3)
    assert count_retention(rows, representations["png_only"]) == expected_tiny_counts(2, 2)
    assert count_retention(rows, representations["png_sidecar"]) == expected_tiny_counts(0, 0)
    assert rows["52009229[0].00280030"]["value"] == [1.0, 2.0]
    assert not rows["00111011"]["countable"] and rows["00111010"]["private"]
    assert rows["00080070"]["presence"] == "empty" and rows["0020000D"]["presence"] == "missing"


def test_empty_denominators_remain_null():
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    result = count_retention(source_inventory(ds), {})
    assert result == {
        "all_eligible": 0,
        "all_matched": 0,
        "all_fraction": None,
        "focused_eligible": 0,
        "focused_matched": 0,
        "focused_fraction": None,
    }


@pytest.mark.parametrize("inverted,png", [(False, [0, 127, 255]), (True, [255, 128, 0])])
def test_hand_calculated_signed_quantization(inverted, png):
    source = np.array([-100, 0, 100], dtype=np.int16)
    result = intensity_metrics(source, np.array(png, dtype=np.uint8), inverted)
    assert result["png_matches_transform"] and result["unique_value_collapse_fraction"] == 0
    assert result["source_unique_values"] == result["export_unique_values"] == 3
    assert result["reconstruction_max_error_stored_units"] == pytest.approx(100 / 255)
    assert result["reconstruction_mae_stored_units"] == pytest.approx(100 / 765)


@pytest.mark.parametrize("inverted,code", [(False, 0), (True, 255)])
def test_constant_intensities_have_zero_reconstruction_error(inverted, code):
    result = intensity_metrics(
        np.full((2, 2), -10), np.full((2, 2), code, dtype=np.uint8), inverted
    )
    assert result["png_matches_transform"] and result["source_unique_values"] == 1
    assert (
        result["unique_value_collapse_fraction"]
        == result["reconstruction_max_error_stored_units"]
        == 0
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("all_eligible", "99999"),
        ("all_matched", "99999"),
        ("focused_eligible", "99999"),
        ("focused_matched", "99999"),
        ("all_fraction", "0.123"),
        ("focused_fraction", "0.123"),
        ("source_unique_values", "99999"),
        ("export_unique_values", "99999"),
        ("unique_value_collapse_fraction", "0.123"),
        ("reconstruction_mae_stored_units", "99999"),
        ("reconstruction_max_error_stored_units", "99999"),
        ("decoded_frames", "99"),
        ("export_frames", "99"),
        ("source_bits_stored", "99"),
        ("export_bits", "99"),
        ("png_matches_transform", "False"),
        ("dimensions_preserved", "False"),
        ("all_fraction", "nan"),
        ("all_fraction", "inf"),
        ("all_matched", "True"),
    ],
)
def test_rehashed_measurement_forgery_rejected(tmp_path, field, value):
    _, manifest, _, out = compared_fixture(tmp_path)
    change_measurement(out, field, value)
    with pytest.raises(ValueError, match="Scientific audit"):
        verify(manifest, out)


def test_coordinated_csv_summary_report_forgery_rejected(tmp_path):
    _, manifest, _, out = compared_fixture(tmp_path)
    alter_counts_together(out)
    with pytest.raises(ValueError, match="Scientific audit"):
        verify(manifest, out)


@pytest.mark.parametrize(
    "key,value",
    [
        ("compared_instances", 99),
        ("modality_coverage", ["PT"]),
        ("distinct_recorded_dates", 99),
        ("source_objects", 99),
        ("excluded_objects", 99),
        ("comparison_passed", False),
        ("first_milestone", True),
        ("cross_modality_complete", True),
        ("intended_instances", 99),
        ("successful_instances", 99),
        ("failed_instances", 99),
        ("completion_status", "partial"),
        ("failures", [{"instance": "instance-000", "error": "invented"}]),
    ],
)
def test_rehashed_summary_report_forgery_rejected(tmp_path, key, value):
    _, manifest, _, out = compared_fixture(tmp_path)
    change_summary(out, key, value)
    with pytest.raises(ValueError, match="Scientific audit"):
        verify(manifest, out)


@pytest.mark.parametrize(
    "target,operation",
    [
        ("comparison.csv", "remove"),
        ("comparison.csv", "duplicate"),
        ("comparison.csv", "unexpected"),
        ("inventory.csv", "remove"),
        ("inventory.csv", "duplicate"),
    ],
)
def test_rehashed_missing_duplicate_unexpected_rows_rejected(tmp_path, target, operation):
    _, manifest, _, out = compared_fixture(tmp_path)
    rows = read_table(out / target)
    row = rows.pop(0) if operation == "remove" else copy.deepcopy(rows[0])
    if operation != "remove":
        row["instance"] = "unexpected" if operation == "unexpected" else row["instance"]
        rows.append(row)
    write_csv(out / target, rows)
    refresh_checksums(out)
    with pytest.raises(ValueError, match="Scientific audit"):
        verify(manifest, out)


@pytest.mark.parametrize(
    "field,value",
    [
        ("value", "[99999]"),
        ("png_only_exact", "False"),
        ("focused", "False"),
        ("countable", "False"),
    ],
)
def test_rehashed_inventory_forgery_rejected(tmp_path, field, value):
    _, manifest, _, out = compared_fixture(tmp_path)
    rows = read_table(out / "inventory.csv")
    row = next(row for row in rows if row["path"] == "00280010")
    row[field] = value
    write_csv(out / "inventory.csv", rows)
    refresh_checksums(out)
    with pytest.raises(ValueError, match="Scientific audit"):
        verify(manifest, out)


@pytest.mark.parametrize(
    "target,replacement",
    [
        ("dicom_experiment.measurements.retention", wrong_count_calculator),
        ("dicom_experiment.measurements.pixel_metrics", wrong_pixel_calculator),
    ],
)
def test_independent_audit_rejects_consistently_wrong_production_calculations(
    tmp_path, target, replacement
):
    _, manifest, _ = fixture(tmp_path)
    with patch(target, side_effect=replacement):
        compare(manifest, tmp_path / "out")
    with pytest.raises(ValueError, match="Scientific audit"):
        verify(manifest, tmp_path / "out")


def test_legacy_complete_artifacts_derive_completion(tmp_path):
    _, manifest, _, out = compared_fixture(tmp_path)
    old = {
        key: value for key, value in load(out / "summary.json").items() if key not in OUTCOME_FIELDS
    }
    synchronize_report(out, old)
    assert verify(manifest, out)["completion_status"] == "complete"


def test_legacy_partial_false_success_claim_rejected(tmp_path):
    manifest = multi_manifest(tmp_path)
    summary = compare(manifest, tmp_path / "out", exporter=SelectiveExporter(["instance-001"]))
    old = {key: value for key, value in summary.items() if key not in OUTCOME_FIELDS}
    old["comparison_passed"] = True
    synchronize_report(tmp_path / "out", old)
    with pytest.raises(ValueError, match="comparison_passed"):
        verify(manifest, tmp_path / "out")


def test_partial_failure_cannot_be_erased_and_rehashed(tmp_path):
    manifest = multi_manifest(tmp_path)
    compare(manifest, tmp_path / "out", exporter=SelectiveExporter(["instance-001"]))
    change_summary(tmp_path / "out", "failures", [])
    with pytest.raises(ValueError, match="partitioned"):
        verify(manifest, tmp_path / "out")


def test_partial_run_with_all_modalities_cannot_claim_cross_modality_completion(tmp_path):
    manifest = cross_modality_manifest(tmp_path)
    result = compare(manifest, tmp_path / "out", exporter=SelectiveExporter(["instance-005"]))
    assert result["modality_coverage"] == ["CT", "DX", "MR", "PT"]
    assert result["distinct_recorded_dates"] == 2 and result["first_milestone"]
    assert result["completion_status"] == "partial" and not result["cross_modality_complete"]
    assert verify(manifest, tmp_path / "out")["completion_status"] == "partial"


@pytest.mark.parametrize("condition", ["dicom", "png_only", "png_sidecar"])
@pytest.mark.parametrize(
    "field", ["all_eligible", "all_matched", "focused_eligible", "focused_matched"]
)
def test_summary_counts_are_rebuilt_from_source_observations(tmp_path, condition, field):
    _, manifest, _, out = compared_fixture(tmp_path)
    summary = load(out / "summary.json")
    summary[condition][field] += 1
    synchronize_report(out, summary)
    with pytest.raises(ValueError, match="Scientific audit"):
        verify(manifest, out)


def test_incomplete_new_outcome_schema_is_rejected(tmp_path):
    _, manifest, _, out = compared_fixture(tmp_path)
    summary = load(out / "summary.json")
    del summary["instance_outcomes"]
    synchronize_report(out, summary)
    with pytest.raises(ValueError, match="outcome schema"):
        verify(manifest, out)


def test_report_only_forgery_is_rejected(tmp_path):
    _, manifest, _, out = compared_fixture(tmp_path)
    original = load(out / "summary.json")
    synchronize_report(out, {**original, "compared_instances": 9})
    dump(out / "summary.json", original)
    refresh_checksums(out)
    with pytest.raises(ValueError, match="report"):
        verify(manifest, out)


def test_hierarchy_conflict_count_is_independently_reconciled(tmp_path):
    _, manifest, _, out = compared_fixture(tmp_path)
    change_summary(out, "hierarchy_conflicts", 99)
    with pytest.raises(ValueError, match="hierarchy_conflicts"):
        verify(manifest, out)


@pytest.mark.parametrize("fault", ["duplicate_sop", "conflicting_study"])
def test_duplicate_or_conflicting_linkage_cannot_satisfy_series_gate(tmp_path, fault):
    manifest = cross_modality_manifest(tmp_path)
    data = load(manifest)
    first = pydicom.dcmread(tmp_path / data["objects"][0]["path"])
    path = tmp_path / data["objects"][1]["path"]
    second = pydicom.dcmread(path)
    keyword = "SOPInstanceUID" if fault == "duplicate_sop" else "StudyInstanceUID"
    setattr(second, keyword, first.SOPInstanceUID)
    second.save_as(path, enforce_file_format=True)
    data["objects"][1]["sha256"] = sha(path)
    dump(manifest, data)
    result = compare(manifest, tmp_path / "out")
    assert result["comparison_passed"] and not result["cross_modality_complete"]
    assert verify(manifest, tmp_path / "out")["completion_status"] == "complete"


def test_failed_instance_cannot_claim_inventory_matches(tmp_path):
    manifest = multi_manifest(tmp_path)
    out = tmp_path / "out"
    compare(manifest, out, exporter=SelectiveExporter(["instance-001"]))
    rows = read_table(out / "inventory.csv")
    failed = next(row for row in rows if row["instance"] == "instance-001")
    failed["png_only_exact"] = "True"
    write_csv(out / "inventory.csv", rows)
    refresh_checksums(out)
    with pytest.raises(ValueError, match="png_only_exact"):
        verify(manifest, out)
