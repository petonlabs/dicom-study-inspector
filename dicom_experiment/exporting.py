"""Inspector export adapter and source/frame provenance construction."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from inspector import inspect

from .artifacts import inside, sha
from .config import METADATA_FILE
from .decoding import normalize_frames
from .pixels import DisplayTransform, read_pngs


class Exporter(Protocol):
    def export(self, source: Path, destination: Path) -> dict: ...


class InspectorExporter:
    def export(self, source, destination):
        return inspect(source, destination)


@dataclass(frozen=True)
class ExportedInstance:
    destination: Path
    inspected_source: Path
    display: dict
    paths: list[Path]
    png: dict
    pixels: np.ndarray
    transform: DisplayTransform


def export_instance(instance, out, decoder, exporter):
    ds = instance.dataset
    destination = inside(out, instance.entry["id"])
    source = decoder.prepare(instance.source, ds, destination)
    result = exporter.export(source, destination)
    paths = sorted(destination.glob("frame-*.png"))
    pixels = normalize_frames(ds, decoder.decode(ds))
    transform = DisplayTransform.from_pixels(pixels, str(ds.PhotometricInterpretation))
    return ExportedInstance(
        destination, source, result["display"], paths, read_pngs(paths), pixels, transform
    )


def frame_records(paths, out):
    return [
        {"index": i, "path": str(path.relative_to(out)), "sha256": sha(path)}
        for i, path in enumerate(paths)
    ]


def source_provenance(instance, exported, out, decoder_name):
    derived = exported.inspected_source != instance.source
    return {
        "instance": instance.entry["id"],
        "source_sha256": instance.entry["sha256"],
        "inspector_input_sha256": sha(exported.inspected_source),
        "derived_native_path": str(exported.inspected_source.relative_to(out)) if derived else None,
        "original_transfer_syntax": str(instance.dataset.file_meta.TransferSyntaxUID),
        "decoder": decoder_name,
        "sidecar_sha256": sha(exported.destination / METADATA_FILE),
    }


def export_provenance(instance, exported, out, decoder_name):
    transform = exported.transform
    return {
        **source_provenance(instance, exported, out, decoder_name),
        "low": transform.low,
        "high": transform.high,
        "inverted": transform.inverted,
        "transform": exported.display["transform"],
        "frames": frame_records(exported.paths, out),
    }
