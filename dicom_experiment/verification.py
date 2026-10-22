"""Offline independent checks of source, sidecar, derivative and frame bindings."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pydicom

from .artifacts import artifact_hashes, inside, load, sha
from .config import CHECKSUMS_FILE, EXPORT_MANIFEST_FILE, METADATA_FILE
from .decoding import Decoder, normalize_frames, select_decoder
from .pixels import DisplayTransform, read_pngs


@dataclass(frozen=True)
class VerificationContext:
    root: Path
    results: Path
    manifest: dict
    decoder: Decoder


def require_hash(path, expected, message):
    if not path.is_file() or sha(path) != expected:
        raise ValueError(message)


def verify_catalog(manifest, root):
    evidence = [
        ("catalog/studies.html", "catalog_sha256"),
        ("catalog/access.html", "access_sha256"),
    ]
    for filename, key in evidence:
        if manifest.get(key):
            require_hash(
                inside(root, filename), manifest[key], "Access/catalog evidence hash mismatch"
            )
    for listing in manifest.get("listings", []):
        require_hash(
            inside(root, listing["path"]), listing["sha256"], "Source listing hash mismatch"
        )


def verify_sources(manifest, root):
    for entry in manifest["objects"]:
        if entry.get("status") == "reserved":
            raise ValueError("Unfinished acquisition reservation: " + entry["id"])
        if entry.get("sha256"):
            require_hash(
                inside(root, entry["path"]),
                entry["sha256"],
                "Source missing or hash mismatch: " + entry["id"],
            )


def verify_artifacts(results):
    checksums, actual = load(results / CHECKSUMS_FILE), artifact_hashes(results)
    if actual.keys() != checksums.keys():
        raise ValueError("Result file set changed")
    for relative, digest in checksums.items():
        if actual[relative] != digest:
            raise ValueError("Output hash mismatch: " + relative)
    return len(checksums)


def verify_sidecar(context, entry, obj):
    side = inside(context.results, obj["instance"]) / METADATA_FILE
    if (
        obj["source_sha256"] != entry["sha256"]
        or load(side)["source_sha256"] != obj["inspector_input_sha256"]
        or sha(side) != obj["sidecar_sha256"]
    ):
        raise ValueError("Sidecar/source mismatch")


def verify_decoder(context, ds, obj):
    syntax = str(ds.file_meta.TransferSyntaxUID)
    if syntax != obj["original_transfer_syntax"] or obj["decoder"] != context.manifest.get(
        "decoder", "native"
    ):
        raise ValueError("Decoder provenance mismatch")


def verify_native(context, entry, obj, pixels):
    if obj["derived_native_path"]:
        path = inside(context.results, obj["derived_native_path"])
        require_hash(path, obj["inspector_input_sha256"], "Derived native/source mismatch")
        if not np.array_equal(pydicom.dcmread(path).pixel_array, pixels):
            raise ValueError("Derived native/source mismatch")
    elif obj["inspector_input_sha256"] != entry["sha256"]:
        raise ValueError("Inspector input hash mismatch")


def verify_transform(ds, pixels, obj):
    transform = DisplayTransform.from_pixels(pixels, str(ds.PhotometricInterpretation))
    if (transform.low, transform.high, transform.inverted) != (
        obj["low"],
        obj["high"],
        obj["inverted"],
    ):
        raise ValueError("Transform provenance mismatch")
    return transform.render(pixels)


def verify_frames(context, obj, expected):
    if [frame["index"] for frame in obj["frames"]] != list(range(len(expected))):
        raise ValueError("Frame index mismatch")
    for frame in obj["frames"]:
        require_hash(inside(context.results, frame["path"]), frame["sha256"], "Frame hash mismatch")
    paths = [inside(context.results, frame["path"]) for frame in obj["frames"]]
    if not np.array_equal(read_pngs(paths)["pixels"], expected):
        raise ValueError("PNG pixels disagree with source transform")


def verify_instance(context, entry, obj):
    verify_sidecar(context, entry, obj)
    ds = pydicom.dcmread(inside(context.root, entry["path"]))
    verify_decoder(context, ds, obj)
    pixels = context.decoder.decode(ds)
    verify_native(context, entry, obj, pixels)
    expected = verify_transform(ds, normalize_frames(ds, pixels), obj)
    verify_frames(context, obj, expected)


def verify_exports(context, export):
    entries = {entry["id"]: entry for entry in context.manifest["objects"]}
    for obj in export["objects"]:
        verify_instance(context, entries[obj["instance"]], obj)


def verification_summary(manifest, export, files):
    return {
        "verified_sources": sum(bool(e.get("sha256")) for e in manifest["objects"]),
        "verified_exports": len(export["objects"]),
        "verified_files": files,
    }


def verify(
    manifest_path: Path | str, results: Path | str, *, decoder_backend: Decoder | None = None
) -> dict:
    manifest_path, results = Path(manifest_path).resolve(), Path(results).resolve()
    manifest, export = load(manifest_path), load(results / EXPORT_MANIFEST_FILE)
    require_hash(manifest_path, export["manifest_sha256"], "Manifest hash mismatch")
    verify_catalog(manifest, manifest_path.parent)
    verify_sources(manifest, manifest_path.parent)
    files = verify_artifacts(results)
    decoder = select_decoder(manifest.get("decoder", "native"), decoder_backend)
    verify_exports(VerificationContext(manifest_path.parent, results, manifest, decoder), export)
    return verification_summary(manifest, export, files)
