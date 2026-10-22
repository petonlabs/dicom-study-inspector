"""Boundary contracts use synthetic objects and offline adapters only."""

import copy
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from comparison_test_support import (
    MemoryTransport,
    RecordingDecoder,
    RecordingExporter,
    archive_object,
    compared_fixture,
    fixture,
    rewrite_export,
    source_helper,
)
from dicom_experiment.acquisition import expand_sample
from dicom_experiment.acquisition_state import BudgetExhausted
from dicom_experiment.artifacts import artifact_hashes, dump, load, sha
from dicom_experiment.comparison import compare, compare_instance, load_instance
from dicom_experiment.decoding import PydicomDecoder
from dicom_experiment.exporting import InspectorExporter
from dicom_experiment.models import SampleRequest
from dicom_experiment.selection import sample_series
from dicom_experiment.verification import verify


@pytest.mark.parametrize("wrapped", [False, True])
def test_decoder_exporter_substitution_contract(tmp_path, wrapped):
    _, manifest, _ = fixture(tmp_path, signed=True)
    decoder = RecordingDecoder() if wrapped else PydicomDecoder()
    exporter = RecordingExporter() if wrapped else InspectorExporter()
    result = compare(manifest, tmp_path / "out", decoder_backend=decoder, exporter=exporter)
    assert result["comparison_passed"] and result["png_only"]["all_matched"] == 2
    assert verify(manifest, tmp_path / "out", decoder_backend=decoder)["verified_exports"] == 1
    if wrapped:
        assert decoder.calls == ["prepare", "decode", "decode"]
        assert len(exporter.calls) == 1


def test_comparison_does_not_mutate_input_inventory(tmp_path):
    _, manifest, _ = fixture(tmp_path)
    instance = load_instance(load(manifest)["objects"][0], tmp_path)
    before = copy.deepcopy(instance.inventory)
    result = compare_instance(
        instance, tmp_path / "out", "native", PydicomDecoder(), InspectorExporter()
    )
    assert instance.inventory == before
    assert all("png_only_exact" in row for row in result.inventory)
    assert all("png_only_exact" not in row for row in instance.inventory)


@pytest.mark.parametrize(
    "key,value",
    [("size", -1), ("size", "2"), ("size", True), ("key", ""), ("key", None), ("etag", None)],
)
def test_malformed_source_rejected_before_network_or_reservation(tmp_path, key, value):
    transport = MemoryTransport()
    source = source_helper(tmp_path, 100, transport=transport)
    obj = {**archive_object(10), key: value}
    with pytest.raises(ValueError):
        source.download(obj, "ct", "CT")
    assert not transport.calls and not source.ledger["objects"]
    assert source.ledger["charged_bytes"] == 0


def assert_persisted_reservation(root, size):
    ledger = load(root / "acquisition-ledger.json")
    assert ledger["charged_bytes"] == size
    assert ledger["objects"][0]["status"] == "reserved"


def test_reservation_is_persisted_before_get(tmp_path):
    path, _, _ = fixture(tmp_path)
    transport = MemoryTransport(path.read_bytes())
    source = source_helper(tmp_path, len(transport.data), transport=transport)
    transport.before_download = lambda: assert_persisted_reservation(tmp_path, len(transport.data))
    entry = source.download(archive_object(len(transport.data)), "ct", "CT")
    assert entry["status"] == "accepted"
    assert transport.calls == ["head", "download"]


@pytest.mark.parametrize("failure", [BudgetExhausted("budget"), ValueError("invalid catalog")])
def test_only_budget_exhaustion_can_freeze_partial_acquisition(tmp_path, failure):
    source = source_helper(tmp_path, 100)
    source.ledger.update(notes=[], first_series="first")
    groups = {"chest": ["chest"], "mr": [], "pt": [], "dx": []}
    with patch("dicom_experiment.acquisition.sample_series", side_effect=failure):
        if isinstance(failure, BudgetExhausted):
            expand_sample(source, groups)
            assert source.ledger["complete"]
        else:
            with pytest.raises(ValueError, match="invalid catalog"):
                expand_sample(source, groups)
            assert not source.ledger.get("complete")


@pytest.mark.parametrize(
    "key,value,message",
    [
        ("low", -999999, "Transform provenance"),
        ("source_sha256", "wrong", "Sidecar/source"),
        ("decoder", "wrong", "Decoder provenance"),
        ("frames", [], "Frame index"),
    ],
)
def test_semantic_verification_survives_recomputed_checksums(tmp_path, key, value, message):
    _, manifest, _, out = compared_fixture(tmp_path)
    rewrite_export(out, key, value)
    with pytest.raises(ValueError, match=message):
        verify(manifest, out)


def change_png_and_rebind_hashes(out):
    frame = out / "instance-000/frame-0000.png"
    with Image.open(frame) as image:
        pixels = np.array(image)
    pixels.flat[0] ^= 1
    Image.fromarray(pixels).save(frame)
    export = load(out / "export_manifest.json")
    export["objects"][0]["frames"][0]["sha256"] = sha(frame)
    dump(out / "export_manifest.json", export)
    dump(out / "checksums.json", artifact_hashes(out))


def test_pixel_verification_is_independent_of_stored_hashes(tmp_path):
    _, manifest, _, out = compared_fixture(tmp_path)
    change_png_and_rebind_hashes(out)
    with pytest.raises(ValueError, match="PNG pixels disagree"):
        verify(manifest, out)


def test_export_failure_cannot_publish_successful_instance(tmp_path):
    _, manifest, _ = fixture(tmp_path)
    exporter = RecordingExporter()
    with patch.object(exporter, "export", side_effect=ValueError("export unavailable")):
        result = compare(manifest, tmp_path / "out", exporter=exporter)
    assert result["compared_instances"] == 0 and result["failures"]
    assert not result["comparison_passed"]


@pytest.mark.parametrize("prefixes", [[], ["first/", "second/"]])
def test_truncated_series_catalog_is_reported_once_even_without_candidates(tmp_path, prefixes):
    source = source_helper(tmp_path, 100)
    source.ledger["notes"] = []
    responses = [(prefixes, [], True)] + [([], [], False) for _ in prefixes]
    with patch.object(source, "listing", side_effect=responses):
        assert sample_series(source, SampleRequest("study/", "ct", "CT", 3)) is None
    assert source.ledger["notes"] == [
        "Series listing truncated at 100: ct",
        "No eligible series within bounded listing: ct",
    ]
