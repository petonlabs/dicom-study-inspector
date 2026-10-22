import copy
import csv
import io
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pydicom
import pytest
from PIL import Image, features
from pydicom.dataset import Dataset
from pydicom.encaps import encapsulate
from pydicom.uid import generate_uid

from dicom_experiment.acquisition import Source, close_interrupted_reservations
from dicom_experiment.artifacts import dump, sha
from dicom_experiment.comparison import compare
from dicom_experiment.metadata import inventory
from dicom_experiment.pixels import read_pngs
from dicom_experiment.structure import chronology, hierarchy, spatial_order
from dicom_experiment.verification import verify
from inspector import InspectionError, inspect
from synthetic import generate


def fixture(tmp_path, *, signed=False, mono1=False, constant=False):
    source = tmp_path / "sample.dcm"
    ds = generate(source, 1)
    ds.Modality = "CT"
    ds.StudyDate = "20220101"
    ds.StudyTime = "12"
    ds.FrameOfReferenceUID = generate_uid()
    ds.ImagePositionPatient = [0, 0, 1]
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.PixelSpacing = [0.5, 0.5]
    if signed:
        pixels = np.arange(-2048, 2048, dtype=np.int16).reshape(64, 64)
        ds.BitsAllocated = ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 1
    else:
        pixels = ds.pixel_array
    if constant:
        pixels[:] = 17
    if mono1:
        ds.PhotometricInterpretation = "MONOCHROME1"
    ds.PixelData = pixels.tobytes()
    ds.save_as(source, enforce_file_format=True)
    manifest = tmp_path / "manifest.json"
    dump(
        manifest,
        {
            "objects": [
                {
                    "id": "instance-000",
                    "path": source.name,
                    "sha256": sha(source),
                    "status": "accepted",
                }
            ]
        },
    )
    return source, manifest, ds


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
    source, manifest, ds = fixture(
        tmp_path, signed=signed, mono1=mono1, constant=constant
    )
    summary = compare(manifest, tmp_path / "out")
    assert summary["compared_instances"] == 1
    assert summary["png_only"]["all_matched"] == 2
    assert (
        summary["png_sidecar"]["all_matched"] < summary["dicom"]["all_matched"]
    )  # file meta not fully retained
    assert verify(manifest, tmp_path / "out")["verified_exports"] == 1
    rows = list(csv.DictReader((tmp_path / "out/comparison.csv").open()))
    side = rows[-1]
    assert side["png_matches_transform"] == "True"
    assert (
        float(side["reconstruction_max_error_stored_units"])
        <= (float(ds.pixel_array.max()) - float(ds.pixel_array.min())) / 255 + 1e-8
    )
    if signed and not constant:
        assert float(side["unique_value_collapse_fraction"]) > 0


def test_inventory_missing_empty_nested_private_binary(tmp_path):
    _, _, ds = fixture(tmp_path)
    ds.Manufacturer = ""
    ds.add_new(0x00111010, "LO", "private text")
    ds.add_new(0x00111011, "OB", b"\x00\x01")
    child = Dataset()
    child.PixelSpacing = [1, 2]
    ds.SharedFunctionalGroupsSequence = [child]
    ds.StudyDate = "bad"
    rows = inventory(ds)
    assert (
        next(r for r in rows if r["keyword"] == "Manufacturer")["presence"] == "empty"
    )
    assert next(r for r in rows if r["keyword"] == "KVP")["presence"] == "missing"
    assert next(r for r in rows if r["tag"] == "00111010")["private"]
    binary = next(r for r in rows if r["tag"] == "00111011")
    assert not binary["countable"] and "binary_value_omitted" in binary["validation"]
    assert any("52009229[0].00280030" == r["path"] for r in rows)
    assert next(r for r in rows if r["keyword"] == "StudyDate")["validation"]
    assert not any(r["tag"] == "7FE00010" for r in rows)


def test_hierarchy_conflicts_and_missing(tmp_path):
    _, _, a = fixture(tmp_path)
    b = copy.deepcopy(a)
    b.StudyInstanceUID = generate_uid()
    c = copy.deepcopy(a)
    del c.SOPInstanceUID
    h = hierarchy([("a", a), ("b", b), ("c", c)])
    assert len(h["studies"]) == 2
    assert {x["finding"] for x in h["conflicts"]} >= {
        "conflicting_sop_linkage",
        "conflicting_series_study_linkage",
        "missing_uid_linkage",
    }
    assert h["chronology"]["ordered_groups_by_clock"][
        "recorded_local_clock_timezone_unknown"
    ][0]["tie_unresolved"]
    assert (
        hierarchy([("a", a), ("a2", a)])["conflicts"][0]["finding"]
        == "duplicate_sop_uid"
    )


def test_geometry_uses_positions_not_instance_number(tmp_path):
    _, _, a = fixture(tmp_path)
    b = copy.deepcopy(a)
    a.InstanceNumber, b.InstanceNumber = 1, 99
    a.ImagePositionPatient, b.ImagePositionPatient = [0, 0, 5], [0, 0, 2]
    s = spatial_order([("a", a), ("b", b)])
    assert s["order"] == ["b", "a"] and s["sample_gaps_mm"] == [3]
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
    if target == "source":
        source.write_bytes(b"corrupt")
    elif target == "missing":
        source.unlink()
    else:
        p = (
            out
            / "instance-000"
            / ("frame-0000.png" if target == "png" else "metadata.json")
        )
        p.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify(manifest, out)


def test_reproducible_offline_and_no_output_overwrite(tmp_path):
    _, manifest, _ = fixture(tmp_path)
    with patch(
        "urllib.request.urlopen", side_effect=AssertionError("Network forbidden")
    ):
        compare(manifest, tmp_path / "one")
        compare(manifest, tmp_path / "two")
        verify(manifest, tmp_path / "one")
    assert (tmp_path / "one/checksums.json").read_bytes() == (
        tmp_path / "two/checksums.json"
    ).read_bytes()
    with pytest.raises(ValueError, match="empty"):
        compare(manifest, tmp_path / "one")


class Response(io.BytesIO):
    def __init__(self, data, size=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if size is None else size)}


def source_helper(tmp_path, max_bytes, max_instances=20):
    (tmp_path / "sources").mkdir(exist_ok=True)
    return Source(
        tmp_path,
        {
            "objects": [],
            "max_instances": max_instances,
            "max_bytes": max_bytes,
            "charged_bytes": 0,
        },
    )


def test_download_caps_before_network(tmp_path):
    src = source_helper(tmp_path, 100, 1)
    with patch(
        "urllib.request.urlopen", side_effect=AssertionError("Network forbidden")
    ):
        with pytest.raises(ValueError, match="budget"):
            src.download({"key": "x", "size": 101, "etag": "x"}, "ct", "CT")
        src.ledger["objects"] = [{"key": "a"}]
        with pytest.raises(ValueError, match="budget"):
            src.download({"key": "x", "size": 1, "etag": "x"}, "ct", "CT")


def test_failed_transfer_is_charged_and_not_retried(tmp_path):
    src = source_helper(tmp_path, 100)
    with patch(
        "urllib.request.urlopen", side_effect=[Response(b"", 10), Response(b"abc", 10)]
    ) as request:
        entry = src.download({"key": "dicom/a", "size": 10, "etag": "x"}, "ct", "CT")
        assert entry["status"] == "excluded"
        assert src.ledger["charged_bytes"] == 10
        assert (
            src.download({"key": "dicom/a", "size": 10, "etag": "x"}, "ct", "CT")
            == entry
        )
        assert request.call_count == 2


def test_unsupported_download_excluded(tmp_path):
    source, _, ds = fixture(tmp_path)
    ds.file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.4.50"
    ds.PixelData = encapsulate([b"fake"])
    ds.save_as(source, enforce_file_format=True)
    data = source.read_bytes()
    src = source_helper(tmp_path, len(data))
    with patch(
        "urllib.request.urlopen", side_effect=[Response(b"", len(data)), Response(data)]
    ):
        entry = src.download(
            {"key": "dicom/a", "size": len(data), "etag": "x"}, "ct", "CT"
        )
    assert (
        entry["status"] == "excluded"
        and "unsupported_transfer_syntax" in entry["reason"]
    )
    assert src.ledger["charged_bytes"] == len(data)


def test_empty_multivalues_not_in_denominator(tmp_path):
    _, _, ds = fixture(tmp_path)
    ds.add_new(0x00111010, "LO", ["", ""])
    ds.add_new(0x00111012, "LO", ["", "real"])
    ds.PatientName = ""
    rows = inventory(ds)
    assert next(r for r in rows if r["tag"] == "00111010")["presence"] == "empty"
    assert next(r for r in rows if r["tag"] == "00111012")["presence"] == "present"
    assert next(r for r in rows if r["keyword"] == "PatientName")["presence"] == "empty"


def test_temporal_precision_overlap_and_utc(tmp_path):
    _, _, a = fixture(tmp_path)
    b = copy.deepcopy(a)
    b.StudyInstanceUID = generate_uid()
    b.SOPInstanceUID = generate_uid()
    b.SeriesInstanceUID = generate_uid()
    a.StudyTime, b.StudyTime = "1200", "1300"
    h = hierarchy([("a", a), ("b", b)])
    groups = h["chronology"]["ordered_groups_by_clock"][
        "recorded_local_clock_timezone_unknown"
    ]
    assert len(groups) == 2 and not any(g["tie_unresolved"] for g in groups)
    a.StudyTime = ""
    h = hierarchy([("a", a), ("b", b)])
    assert h["chronology"]["ordered_groups_by_clock"][
        "recorded_local_clock_timezone_unknown"
    ][0]["tie_unresolved"]
    a.StudyTime, b.StudyTime = "1400", "1200"
    a.TimezoneOffsetFromUTC, b.TimezoneOffsetFromUTC = "+0300", "+0000"
    groups = hierarchy([("a", a), ("b", b)])["chronology"]["ordered_groups_by_clock"][
        "UTC"
    ]
    assert groups[0]["studies"] == [str(a.StudyInstanceUID)]


def test_metric_failure_cannot_pass_milestone(tmp_path):
    _, manifest, _ = fixture(tmp_path)
    with patch(
        "dicom_experiment.comparison.retention", side_effect=ValueError("metric failed")
    ):
        result = compare(manifest, tmp_path / "out")
    assert result["compared_instances"] == 0
    assert not result["first_milestone"] and not result["cross_modality_complete"]
    assert result["failures"]


def test_optional_lossless_adapter_preserves_source_and_inspector(
    tmp_path, monkeypatch
):
    pytest.importorskip("gdcm")
    if not features.check("jpg_2000"):
        pytest.skip("Pillow JPEG2000 encoder unavailable for synthetic fixture")
    source, manifest, ds = fixture(tmp_path)
    pixels = np.arange(4096, dtype=np.uint16).reshape(64, 64)
    encoded = io.BytesIO()
    Image.fromarray(pixels).save(encoded, format="JPEG2000", irreversible=False)
    ds.BitsAllocated = ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.4.90"
    ds.PixelData = encapsulate([encoded.getvalue()])
    ds.save_as(source, enforce_file_format=True)
    digest = sha(source)
    m = json.loads(manifest.read_text())
    m["objects"][0]["sha256"] = digest
    m["decoder"] = "gdcm"
    dump(manifest, m)
    with pytest.raises(InspectionError, match="unsupported"):
        inspect(source, tmp_path / "original-inspector")
    monkeypatch.chdir(tmp_path)
    summary = compare(Path("manifest.json"), Path("out"))
    assert summary["comparison_passed"] and not summary["first_milestone"]
    assert sha(source) == digest
    assert verify(manifest, tmp_path / "out")["verified_exports"] == 1
    side = json.loads((tmp_path / "out/instance-000/metadata.json").read_text())
    export = json.loads((tmp_path / "out/export_manifest.json").read_text())["objects"][
        0
    ]
    assert side["source_sha256"] == export["inspector_input_sha256"] != digest
    assert export["source_sha256"] == digest
    native = pydicom.dcmread(tmp_path / "out/instance-000/decoded-native.dcm")
    assert np.array_equal(native.pixel_array, pixels)
    assert native.SOPInstanceUID == ds.SOPInstanceUID


def test_synthetic_cannot_satisfy_real_data_gate(tmp_path):
    _, manifest, _ = fixture(tmp_path)
    summary = compare(manifest, tmp_path / "out")
    assert summary["comparison_passed"]
    assert not summary["first_milestone"] and not summary["cross_modality_complete"]


def test_relative_cli_style_paths(tmp_path, monkeypatch):
    _, _, _ = fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = compare(Path("manifest.json"), Path("relative-results"))
    assert result["comparison_passed"]
    assert (
        verify(Path("manifest.json"), Path("relative-results"))["verified_exports"] == 1
    )


def test_original_hash_retained_on_decoder_upgrade(tmp_path):
    src = source_helper(tmp_path, 100)
    p = tmp_path / "sources/one.dcm"
    p.write_bytes(b"previously downloaded")
    entry = {
        "id": "one",
        "key": "dicom/one",
        "status": "excluded",
        "reason": "unsupported_transfer_syntax:1.2.840.10008.1.2.4.70",
        "transfer_syntax": "1.2.840.10008.1.2.4.70",
        "actual_modality": "CT",
        "study_uid": "1",
        "series_uid": "2",
        "sop_uid": "3",
        "path": "sources/one.dcm",
        "sha256": sha(p),
    }
    src.ledger.update(objects=[entry], charged_bytes=22, decoder="gdcm")
    with patch("urllib.request.urlopen", side_effect=AssertionError("No redownload")):
        result = src.download(
            {"key": "dicom/one", "size": 22, "etag": "x"}, "first_ct", "CT"
        )
    assert result["status"] == "accepted" and result["previous_exclusion"]
    assert src.ledger["charged_bytes"] == 22 and len(src.ledger["objects"]) == 1


def test_interrupted_reservation_is_finalized_without_budget_refund(tmp_path):
    (tmp_path / "partial.dcm").write_bytes(b"partial")
    ledger = {
        "charged_bytes": 100,
        "objects": [
            {"id": "one", "status": "reserved", "path": "partial.dcm", "size": 100}
        ],
    }
    close_interrupted_reservations(tmp_path, ledger)
    assert ledger["charged_bytes"] == 100
    assert ledger["objects"][0]["status"] == "excluded"
    assert ledger["objects"][0]["downloaded_bytes"] == 7
    assert ledger["objects"][0]["sha256"] == sha(tmp_path / "partial.dcm")


def test_valid_numeric_wrappers_and_lut_lists_are_not_malformed(tmp_path):
    _, _, ds = fixture(tmp_path)
    ds.RepetitionTime = "123.5"
    ds.InstanceNumber = "3"
    ds.add_new(0x00283002, "US", [3, 0, 16])
    rows = inventory(ds)
    for keyword in [
        "PixelSpacing",
        "RepetitionTime",
        "InstanceNumber",
        "LUTDescriptor",
    ]:
        selected = [r for r in rows if r["keyword"] == keyword]
        assert selected and all(
            "invalid_vr_value" not in r["validation"] for r in selected
        )
