import copy
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image
from pydicom.uid import generate_uid

from comparison_test_support import (
    LOCAL_CLOCK,
    Response,
    archive_object,
    assert_lossless_provenance,
    bind_decoder,
    corrupt_artifact,
    download_bytes,
    fixture,
    lossless_file,
    metadata_edge_cases,
    pixel_assertions,
    row_for,
    separate_study,
    source_helper,
    unsupported_file,
    upgrade_entry,
)
from dicom_experiment.acquisition import close_interrupted_reservations
from dicom_experiment.artifacts import sha
from dicom_experiment.comparison import compare
from dicom_experiment.metadata import inventory
from dicom_experiment.pixels import read_pngs
from dicom_experiment.structure import chronology, hierarchy, spatial_order
from dicom_experiment.verification import verify
from inspector import InspectionError, inspect


@pytest.mark.parametrize(
    "signed,mono1,constant",
    [
        (False, False, False),
        (True, False, False),
        (True, True, False),
        (True, False, True),
        (True, True, True),
    ],
)
def test_real_pipeline_synthetic_pixels(tmp_path, signed, mono1, constant):
    _, manifest, ds = fixture(tmp_path, signed=signed, mono1=mono1, constant=constant)
    summary = compare(manifest, tmp_path / "out")
    assert summary["compared_instances"] == 1
    assert summary["png_only"]["all_matched"] == 2
    assert summary["png_sidecar"]["all_matched"] < summary["dicom"]["all_matched"]
    assert verify(manifest, tmp_path / "out")["verified_exports"] == 1
    pixel_assertions(tmp_path / "out", ds, signed, constant)


def test_inventory_missing_empty_nested_private_binary(tmp_path):
    _, _, ds = fixture(tmp_path)
    rows = inventory(metadata_edge_cases(ds))
    assert row_for(rows, "keyword", "Manufacturer")["presence"] == "empty"
    assert row_for(rows, "keyword", "KVP")["presence"] == "missing"
    assert row_for(rows, "tag", "00111010")["private"]
    binary = row_for(rows, "tag", "00111011")
    assert not binary["countable"] and "binary_value_omitted" in binary["validation"]
    assert any("52009229[0].00280030" == row["path"] for row in rows)
    assert row_for(rows, "keyword", "StudyDate")["validation"]
    assert not any(row["tag"] == "7FE00010" for row in rows)


def test_hierarchy_conflicts_and_missing(tmp_path):
    _, _, a = fixture(tmp_path)
    b, c = copy.deepcopy(a), copy.deepcopy(a)
    b.StudyInstanceUID = generate_uid()
    del c.SOPInstanceUID
    result = hierarchy([("a", a), ("b", b), ("c", c)])
    assert len(result["studies"]) == 2
    assert {x["finding"] for x in result["conflicts"]} >= {
        "conflicting_sop_linkage",
        "conflicting_series_study_linkage",
        "missing_uid_linkage",
    }
    assert result["chronology"]["ordered_groups_by_clock"][LOCAL_CLOCK][0]["tie_unresolved"]
    assert hierarchy([("a", a), ("a2", a)])["conflicts"][0]["finding"] == "duplicate_sop_uid"


def test_geometry_uses_positions_not_instance_number(tmp_path):
    _, _, a = fixture(tmp_path)
    b = copy.deepcopy(a)
    a.InstanceNumber, b.InstanceNumber = 1, 99
    a.ImagePositionPatient, b.ImagePositionPatient = [0, 0, 5], [0, 0, 2]
    result = spatial_order([("a", a), ("b", b)])
    assert result["order"] == ["b", "a"] and result["sample_gaps_mm"] == [3]
    b.ImageOrientationPatient = [0, 1, 0, 1, 0, 0]
    assert spatial_order([("a", a), ("b", b)])["status"] == "unresolved"
    b.ImageOrientationPatient = a.ImageOrientationPatient
    b.FrameOfReferenceUID = generate_uid()
    assert spatial_order([("a", a), ("b", b)])["status"] == "unresolved"


def test_time_precision_and_absence(tmp_path):
    _, _, ds = fixture(tmp_path)
    assert chronology(ds)["precision"] == "hour"
    assert "timezone_unknown" in chronology(ds)["findings"]
    ds.StudyTime = "1230"
    assert chronology(ds)["precision"] == "minute"
    del ds.StudyDate
    assert not chronology(ds)["valid_date"]


def test_png_reader_has_no_reference_access(tmp_path):
    image = tmp_path / "neutral.png"
    Image.fromarray(np.arange(12, dtype=np.uint8).reshape(3, 4)).save(image)
    with patch("pydicom.dcmread", side_effect=AssertionError("DICOM access forbidden")):
        result = read_pngs([image])
    assert result["Rows"] == 3 and result["Columns"] == 4
    assert result["embedded_metadata"] == [{}]
    assert "StudyInstanceUID" not in result


@pytest.mark.parametrize("target", ["source", "png", "sidecar", "missing"])
def test_verify_rejects_tampering(tmp_path, target):
    source, manifest, _ = fixture(tmp_path)
    out = tmp_path / "out"
    compare(manifest, out)
    corrupt_artifact(source, out, target)
    with pytest.raises(ValueError, match="hash mismatch"):
        verify(manifest, out)


def test_reproducible_offline_and_no_output_overwrite(tmp_path):
    _, manifest, _ = fixture(tmp_path)
    with patch("urllib.request.urlopen", side_effect=AssertionError("Network forbidden")):
        compare(manifest, tmp_path / "one")
        compare(manifest, tmp_path / "two")
        verify(manifest, tmp_path / "one")
    assert (tmp_path / "one/checksums.json").read_bytes() == (
        tmp_path / "two/checksums.json"
    ).read_bytes()
    with pytest.raises(ValueError, match="empty"):
        compare(manifest, tmp_path / "one")


def test_download_caps_before_network(tmp_path):
    src = source_helper(tmp_path, 100, 1)
    with patch("urllib.request.urlopen", side_effect=AssertionError("Network forbidden")):
        with pytest.raises(ValueError, match="budget"):
            src.download(archive_object(101), "ct", "CT")
        src.ledger["objects"] = [{"key": "a"}]
        with pytest.raises(ValueError, match="budget"):
            src.download(archive_object(1), "ct", "CT")


def test_failed_transfer_is_charged_and_not_retried(tmp_path):
    src = source_helper(tmp_path, 100)
    responses = [Response(b"", 10), Response(b"abc", 10)]
    with patch("urllib.request.urlopen", side_effect=responses) as request:
        entry = src.download(archive_object(10), "ct", "CT")
        assert entry["status"] == "excluded"
        assert src.ledger["charged_bytes"] == 10
        assert src.download(archive_object(10), "ct", "CT") == entry
        assert request.call_count == 2


def test_unsupported_download_excluded(tmp_path):
    source, _, ds = fixture(tmp_path)
    data = unsupported_file(source, ds)
    src = source_helper(tmp_path, len(data))
    entry = download_bytes(src, data)
    assert entry["status"] == "excluded" and "unsupported_transfer_syntax" in entry["reason"]
    assert src.ledger["charged_bytes"] == len(data)


def test_empty_multivalues_not_in_denominator(tmp_path):
    _, _, ds = fixture(tmp_path)
    ds.add_new(0x00111010, "LO", ["", ""])
    ds.add_new(0x00111012, "LO", ["", "real"])
    ds.PatientName = ""
    rows = inventory(ds)
    assert row_for(rows, "tag", "00111010")["presence"] == "empty"
    assert row_for(rows, "tag", "00111012")["presence"] == "present"
    assert row_for(rows, "keyword", "PatientName")["presence"] == "empty"


@pytest.mark.parametrize(
    "first_time,second_time,tied", [("1200", "1300", False), ("", "1300", True)]
)
def test_temporal_precision_overlap(tmp_path, first_time, second_time, tied):
    _, _, a = fixture(tmp_path)
    b = separate_study(a)
    a.StudyTime, b.StudyTime = first_time, second_time
    groups = hierarchy([("a", a), ("b", b)])["chronology"]["ordered_groups_by_clock"][LOCAL_CLOCK]
    assert len(groups) == (1 if tied else 2)
    assert all(group["tie_unresolved"] == tied for group in groups)


def test_temporal_utc_order(tmp_path):
    _, _, a = fixture(tmp_path)
    b = separate_study(a)
    a.StudyTime, b.StudyTime = "1400", "1200"
    a.TimezoneOffsetFromUTC, b.TimezoneOffsetFromUTC = "+0300", "+0000"
    groups = hierarchy([("a", a), ("b", b)])["chronology"]["ordered_groups_by_clock"]["UTC"]
    assert groups[0]["studies"] == [str(a.StudyInstanceUID)]


def test_metric_failure_cannot_pass_milestone(tmp_path):
    _, manifest, _ = fixture(tmp_path)
    with patch("dicom_experiment.measurements.retention", side_effect=ValueError("metric failed")):
        result = compare(manifest, tmp_path / "out")
    assert result["compared_instances"] == 0
    assert not result["first_milestone"] and not result["cross_modality_complete"]
    assert result["failures"]


def test_optional_lossless_adapter_preserves_source_and_inspector(tmp_path, monkeypatch):
    source, manifest, ds = fixture(tmp_path)
    pixels = lossless_file(source, ds)
    digest = sha(source)
    bind_decoder(manifest, source)
    with pytest.raises(InspectionError, match="unsupported"):
        inspect(source, tmp_path / "original-inspector")
    monkeypatch.chdir(tmp_path)
    summary = compare(Path("manifest.json"), Path("out"))
    assert summary["comparison_passed"] and not summary["first_milestone"]
    assert sha(source) == digest
    assert verify(manifest, tmp_path / "out")["verified_exports"] == 1
    assert_lossless_provenance(tmp_path / "out", digest, ds, pixels)


def test_synthetic_cannot_satisfy_real_data_gate(tmp_path):
    _, manifest, _ = fixture(tmp_path)
    summary = compare(manifest, tmp_path / "out")
    assert summary["comparison_passed"]
    assert not summary["first_milestone"] and not summary["cross_modality_complete"]


def test_relative_cli_style_paths(tmp_path, monkeypatch):
    fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = compare(Path("manifest.json"), Path("relative-results"))
    assert result["comparison_passed"]
    assert verify(Path("manifest.json"), Path("relative-results"))["verified_exports"] == 1


def test_original_hash_retained_on_decoder_upgrade(tmp_path):
    src = source_helper(tmp_path, 100)
    src.ledger.update(objects=[upgrade_entry(tmp_path)], charged_bytes=22, decoder="gdcm")
    with patch("urllib.request.urlopen", side_effect=AssertionError("No redownload")):
        result = src.download(archive_object(22, "dicom/one"), "first_ct", "CT")
    assert result["status"] == "accepted" and result["previous_exclusion"]
    assert src.ledger["charged_bytes"] == 22 and len(src.ledger["objects"]) == 1


def test_interrupted_reservation_is_finalized_without_budget_refund(tmp_path):
    (tmp_path / "partial.dcm").write_bytes(b"partial")
    ledger = {
        "charged_bytes": 100,
        "objects": [{"id": "one", "status": "reserved", "path": "partial.dcm", "size": 100}],
    }
    close_interrupted_reservations(tmp_path, ledger)
    assert ledger["charged_bytes"] == 100
    assert ledger["objects"][0]["status"] == "excluded"
    assert ledger["objects"][0]["downloaded_bytes"] == 7
    assert ledger["objects"][0]["sha256"] == sha(tmp_path / "partial.dcm")


@pytest.mark.parametrize(
    "keyword", ["PixelSpacing", "RepetitionTime", "InstanceNumber", "LUTDescriptor"]
)
def test_valid_numeric_wrappers_and_lut_lists_are_not_malformed(tmp_path, keyword):
    _, _, ds = fixture(tmp_path)
    ds.RepetitionTime, ds.InstanceNumber = "123.5", "3"
    ds.add_new(0x00283002, "US", [3, 0, 16])
    selected = [row for row in inventory(ds) if row["keyword"] == keyword]
    assert selected and all("invalid_vr_value" not in row["validation"] for row in selected)
