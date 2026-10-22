"""Synthetic builders and assertions shared by comparison and adapter contract tests."""

import copy
import csv
import io
from unittest.mock import patch

import numpy as np
import pydicom
import pytest
from PIL import Image, features
from pydicom.dataset import Dataset
from pydicom.encaps import encapsulate
from pydicom.uid import generate_uid

from dicom_experiment.acquisition import Source
from dicom_experiment.artifacts import artifact_hashes, dump, load, sha
from dicom_experiment.comparison import compare
from dicom_experiment.decoding import PydicomDecoder
from dicom_experiment.exporting import InspectorExporter
from synthetic import generate

LOCAL_CLOCK = "recorded_local_clock_timezone_unknown"


def source_entry(source):
    return {"id": "instance-000", "path": source.name, "sha256": sha(source), "status": "accepted"}


def set_geometry(ds):
    ds.Modality, ds.StudyDate, ds.StudyTime = "CT", "20220101", "12"
    ds.FrameOfReferenceUID = generate_uid()
    ds.ImagePositionPatient = [0, 0, 1]
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.PixelSpacing = [0.5, 0.5]


def signed_pixels(ds):
    ds.BitsAllocated = ds.BitsStored = 16
    ds.HighBit, ds.PixelRepresentation = 15, 1
    return np.arange(-2048, 2048, dtype=np.int16).reshape(64, 64)


def fixture(tmp_path, *, signed=False, mono1=False, constant=False):
    source = tmp_path / "sample.dcm"
    ds = generate(source, 1)
    set_geometry(ds)
    pixels = signed_pixels(ds) if signed else ds.pixel_array
    if constant:
        pixels[:] = 17
    if mono1:
        ds.PhotometricInterpretation = "MONOCHROME1"
    ds.PixelData = pixels.tobytes()
    ds.save_as(source, enforce_file_format=True)
    manifest = tmp_path / "manifest.json"
    dump(manifest, {"objects": [source_entry(source)]})
    return source, manifest, ds


def pixel_assertions(out, ds, signed, constant):
    with (out / "comparison.csv").open() as stream:
        side = list(csv.DictReader(stream))[-1]
    assert side["png_matches_transform"] == "True"
    bound = (float(ds.pixel_array.max()) - float(ds.pixel_array.min())) / 255 + 1e-8
    assert float(side["reconstruction_max_error_stored_units"]) <= bound
    if signed and not constant:
        assert float(side["unique_value_collapse_fraction"]) > 0


def metadata_edge_cases(ds):
    ds.Manufacturer = ""
    ds.add_new(0x00111010, "LO", "private text")
    ds.add_new(0x00111011, "OB", b"\x00\x01")
    child = Dataset()
    child.PixelSpacing = [1, 2]
    ds.SharedFunctionalGroupsSequence = [child]
    with pytest.warns(UserWarning, match="Invalid value"):
        ds.StudyDate = "bad"
    return ds


def row_for(rows, key, value):
    return next(row for row in rows if row[key] == value)


def separate_study(ds):
    other = copy.deepcopy(ds)
    other.StudyInstanceUID = generate_uid()
    other.SOPInstanceUID = generate_uid()
    other.SeriesInstanceUID = generate_uid()
    return other


def corrupt_artifact(source, out, target):
    if target == "missing":
        source.unlink()
        return
    filename = "frame-0000.png" if target == "png" else "metadata.json"
    path = source if target == "source" else out / "instance-000" / filename
    path.write_bytes(b"corrupt")


class Response(io.BytesIO):
    def __init__(self, data, size=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if size is None else size)}


def source_helper(tmp_path, max_bytes, max_instances=20, transport=None):
    (tmp_path / "sources").mkdir(exist_ok=True)
    ledger = {
        "objects": [],
        "max_instances": max_instances,
        "max_bytes": max_bytes,
        "charged_bytes": 0,
    }
    return Source(tmp_path, ledger, transport)


def archive_object(size, key="dicom/a"):
    return {"key": key, "size": size, "etag": "x"}


def unsupported_file(source, ds):
    ds.file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.4.50"
    ds.PixelData = encapsulate([b"fake"])
    ds.save_as(source, enforce_file_format=True)
    return source.read_bytes()


def download_bytes(src, data):
    responses = [Response(b"", len(data)), Response(data)]
    with patch("urllib.request.urlopen", side_effect=responses):
        return src.download(archive_object(len(data)), "ct", "CT")


def lossless_file(source, ds):
    pytest.importorskip("gdcm")
    if not features.check("jpg_2000"):
        pytest.skip("Pillow JPEG2000 encoder unavailable for synthetic fixture")
    pixels = np.arange(4096, dtype=np.uint16).reshape(64, 64)
    encoded = io.BytesIO()
    Image.fromarray(pixels).save(encoded, format="JPEG2000", irreversible=False)
    ds.BitsAllocated = ds.BitsStored = 16
    ds.HighBit, ds.PixelRepresentation = 15, 0
    ds.file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.4.90"
    ds.PixelData = encapsulate([encoded.getvalue()])
    ds.save_as(source, enforce_file_format=True)
    return pixels


def bind_decoder(manifest, source):
    data = load(manifest)
    data["objects"][0]["sha256"] = sha(source)
    data["decoder"] = "gdcm"
    dump(manifest, data)


def assert_lossless_provenance(out, digest, ds, pixels):
    side = load(out / "instance-000/metadata.json")
    export = load(out / "export_manifest.json")["objects"][0]
    assert side["source_sha256"] == export["inspector_input_sha256"] != digest
    assert export["source_sha256"] == digest
    native = pydicom.dcmread(out / "instance-000/decoded-native.dcm")
    assert np.array_equal(native.pixel_array, pixels)
    assert native.SOPInstanceUID == ds.SOPInstanceUID


def upgrade_entry(tmp_path):
    path = tmp_path / "sources/one.dcm"
    path.write_bytes(b"previously downloaded")
    return {
        "id": "one",
        "key": "dicom/one",
        "status": "excluded",
        "reason": "unsupported_transfer_syntax:1.2.840.10008.1.2.4.70",
        "transfer_syntax": "1.2.840.10008.1.2.4.70",
        "actual_modality": "CT",
        **dict(study_uid="1", series_uid="2", sop_uid="3"),
        "path": "sources/one.dcm",
        "sha256": sha(path),
    }


def compared_fixture(tmp_path):
    source, manifest, ds = fixture(tmp_path)
    out = tmp_path / "out"
    compare(manifest, out)
    return source, manifest, ds, out


def rewrite_export(out, key, value):
    export = load(out / "export_manifest.json")
    export["objects"][0][key] = value
    dump(out / "export_manifest.json", export)
    dump(out / "checksums.json", artifact_hashes(out))


class RecordingDecoder:
    def __init__(self, name="native"):
        self.backend = PydicomDecoder(name)
        self.calls = []

    def decode(self, dataset):
        self.calls.append("decode")
        return self.backend.decode(dataset)

    def prepare(self, source, dataset, destination):
        self.calls.append("prepare")
        return self.backend.prepare(source, dataset, destination)


class RecordingExporter:
    def __init__(self):
        self.calls = []

    def export(self, source, destination):
        self.calls.append((source, destination))
        return InspectorExporter().export(source, destination)


class MemoryTransport:
    def __init__(self, data=b"", listing=None):
        self.data, self.listing = data, listing
        self.calls = []
        self.before_download = None

    def metadata(self, url):
        self.calls.append("metadata")
        return self.listing

    def content_length(self, url):
        self.calls.append("head")
        return len(self.data)

    def download(self, url, destination, expected_size):
        self.calls.append("download")
        if self.before_download is not None:
            self.before_download()
        assert expected_size == len(self.data)
        destination.write_bytes(self.data)
        return len(self.data)
