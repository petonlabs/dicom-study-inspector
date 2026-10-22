"""Offline verification of source, derivative, sidecar, frame and artifact bindings."""

from pathlib import Path

import numpy as np
import pydicom

from .artifacts import artifact_hashes, inside, load, sha
from .config import CHECKSUMS_FILE, EXPORT_MANIFEST_FILE, METADATA_FILE
from .pixels import DisplayTransform, decoded, read_pngs


def verify(manifest_path: Path | str, results: Path | str) -> dict:
    manifest_path, results = Path(manifest_path).resolve(), Path(results).resolve()
    manifest, export = load(manifest_path), load(results / EXPORT_MANIFEST_FILE)
    if export["manifest_sha256"] != sha(manifest_path):
        raise ValueError("Manifest hash mismatch")
    for filename, key in [
        ("catalog/studies.html", "catalog_sha256"),
        ("catalog/access.html", "access_sha256"),
    ]:
        if (
            manifest.get(key)
            and sha(inside(manifest_path.parent, filename)) != manifest[key]
        ):
            raise ValueError("Access/catalog evidence hash mismatch")
    for listing in manifest.get("listings", []):
        if sha(inside(manifest_path.parent, listing["path"])) != listing["sha256"]:
            raise ValueError("Source listing hash mismatch")
    for entry in manifest["objects"]:
        if entry.get("status") == "reserved":
            raise ValueError("Unfinished acquisition reservation: " + entry["id"])
        if entry.get("sha256"):
            path = inside(manifest_path.parent, entry["path"])
            if not path.is_file() or sha(path) != entry["sha256"]:
                raise ValueError("Source missing or hash mismatch: " + entry["id"])
    checksums = load(results / CHECKSUMS_FILE)
    actual_hashes = artifact_hashes(results)
    if actual_hashes.keys() != checksums.keys():
        raise ValueError("Result file set changed")
    for relative, digest in checksums.items():
        if actual_hashes[relative] != digest:
            raise ValueError("Output hash mismatch: " + relative)
    entries = {e["id"]: e for e in manifest["objects"]}
    for obj in export["objects"]:
        entry = entries[obj["instance"]]
        side = inside(results, obj["instance"]) / METADATA_FILE
        if (
            obj["source_sha256"] != entry["sha256"]
            or load(side)["source_sha256"] != obj["inspector_input_sha256"]
            or sha(side) != obj["sidecar_sha256"]
        ):
            raise ValueError("Sidecar/source mismatch")
        ds = pydicom.dcmread(inside(manifest_path.parent, entry["path"]))
        if str(ds.file_meta.TransferSyntaxUID) != obj[
            "original_transfer_syntax"
        ] or obj["decoder"] != manifest.get("decoder", "native"):
            raise ValueError("Decoder provenance mismatch")
        pixels = decoded(ds, manifest.get("decoder", "native"))
        if obj["derived_native_path"]:
            native_path = inside(results, obj["derived_native_path"])
            if sha(native_path) != obj["inspector_input_sha256"] or not np.array_equal(
                pydicom.dcmread(native_path).pixel_array, pixels
            ):
                raise ValueError("Derived native/source mismatch")
        elif obj["inspector_input_sha256"] != entry["sha256"]:
            raise ValueError("Inspector input hash mismatch")
        if int(ds.get("NumberOfFrames", 1)) == 1:
            pixels = pixels[np.newaxis, ...]
        transform = DisplayTransform.from_pixels(
            pixels, str(ds.PhotometricInterpretation)
        )
        if (transform.low, transform.high, transform.inverted) != (
            obj["low"],
            obj["high"],
            obj["inverted"],
        ):
            raise ValueError("Transform provenance mismatch")
        expected = transform.render(pixels)
        if [f["index"] for f in obj["frames"]] != list(range(len(pixels))):
            raise ValueError("Frame index mismatch")
        for frame in obj["frames"]:
            if sha(inside(results, frame["path"])) != frame["sha256"]:
                raise ValueError("Frame hash mismatch")
        read = read_pngs([inside(results, f["path"]) for f in obj["frames"]])
        if not np.array_equal(read["pixels"], expected):
            raise ValueError("PNG pixels disagree with source transform")
    return {
        "verified_sources": sum(bool(e.get("sha256")) for e in manifest["objects"]),
        "verified_exports": len(export["objects"]),
        "verified_files": len(checksums),
    }
