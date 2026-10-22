"""Local decoder adapter and a single definition of the measured display transform."""

import copy
from dataclasses import dataclass

import numpy as np
import pydicom
from PIL import Image

from .config import LOSSLESS_SYNTAXES, MAX_DECODED_SAMPLES


def decoded(ds, decoder="native"):
    count, rows, cols, samples = [
        int(ds.get(k, 1))
        for k in ("NumberOfFrames", "Rows", "Columns", "SamplesPerPixel")
    ]
    if (
        min(count, rows, cols, samples) < 1
        or count * rows * cols * samples > MAX_DECODED_SAMPLES
    ):
        raise ValueError("Decoded sample limit exceeded or invalid dimensions")
    syntax = str(ds.file_meta.TransferSyntaxUID)
    if ds.file_meta.TransferSyntaxUID.is_compressed:
        if decoder != "gdcm" or syntax not in LOSSLESS_SYNTAXES:
            raise ValueError("Unsupported compressed syntax for experiment")
        return pydicom.pixels.pixel_array(ds, decoding_plugin="gdcm")
    return ds.pixel_array


def inspector_input(source, ds, dest, decoder):
    if not ds.file_meta.TransferSyntaxUID.is_compressed:
        return source
    if (
        decoder != "gdcm"
        or str(ds.file_meta.TransferSyntaxUID) not in LOSSLESS_SYNTAXES
    ):
        raise ValueError("Unsupported compressed syntax for experiment")
    pixels = decoded(ds, decoder)  # Check bounds before decompression or persistence.
    native = copy.deepcopy(ds)
    native.decompress(decoding_plugin="gdcm", generate_instance_uid=False)
    if not np.array_equal(native.pixel_array, pixels):
        raise ValueError("Decompression changed decoded pixels")
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "decoded-native.dcm"
    native.save_as(path, enforce_file_format=True)
    return path


def read_pngs(paths):
    """PNG-only reader: accepts PNG paths, no source/sidecar/manifest arguments."""
    arrays, chunks = [], []
    for path in paths:
        with Image.open(path) as im:
            arrays.append(np.array(im))
            chunks.append(dict(im.info))
    if not arrays:
        raise ValueError("No PNG frames")
    pixels = np.stack(arrays)
    return {
        "Rows": pixels.shape[1],
        "Columns": pixels.shape[2],
        "frame_count": len(arrays),
        "bits": pixels.dtype.itemsize * 8,
        "pixels": pixels,
        "embedded_metadata": chunks,
    }


@dataclass(frozen=True)
class DisplayTransform:
    """The existing inspector's display contract, shared by measurement/verification."""

    low: float
    high: float
    inverted: bool

    @classmethod
    def from_pixels(cls, pixels: np.ndarray, photometric: str) -> "DisplayTransform":
        return cls(
            float(pixels.min()), float(pixels.max()), photometric == "MONOCHROME1"
        )

    def render(self, pixels: np.ndarray) -> np.ndarray:
        if self.high == self.low:
            shown = np.zeros_like(pixels, dtype=np.uint8)
        else:
            shown = (
                (pixels.astype(float) - self.low) * 255 / (self.high - self.low)
            ).astype(np.uint8)
        return 255 - shown if self.inverted else shown

    def reconstruct(self, shown: np.ndarray) -> np.ndarray:
        restored = (255 - shown if self.inverted else shown).astype(float)
        if self.high == self.low:
            return np.full_like(restored, self.low)
        return restored * (self.high - self.low) / 255 + self.low


def frame_array(ds, decoder: str = "native") -> np.ndarray:
    pixels = decoded(ds, decoder)
    return pixels[np.newaxis, ...] if int(ds.get("NumberOfFrames", 1)) == 1 else pixels


def pixel_metrics(
    pixels: np.ndarray, png: np.ndarray, transform: DisplayTransform
) -> dict:
    error = np.abs(transform.reconstruct(png) - pixels.astype(float))
    source_unique, export_unique = len(np.unique(pixels)), len(np.unique(png))
    return {
        "dimensions_preserved": pixels.shape == png.shape,
        "source_unique_values": source_unique,
        "export_unique_values": export_unique,
        "unique_value_collapse_fraction": 1 - export_unique / source_unique,
        "png_matches_transform": np.array_equal(png, transform.render(pixels)),
        "reconstruction_mae_stored_units": float(error.mean()),
        "reconstruction_max_error_stored_units": float(error.max()),
    }
