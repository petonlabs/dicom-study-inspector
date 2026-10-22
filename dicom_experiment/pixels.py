"""PNG-only reading and the shared immutable display transformation contract."""

from dataclasses import dataclass

import numpy as np
from PIL import Image

from .decoding import PydicomDecoder, normalize_frames


def decoded(ds, decoder="native"):
    """Compatibility entry point for callers selecting a decoder by policy name."""
    return PydicomDecoder(decoder).decode(ds)


def inspector_input(source, ds, dest, decoder):
    """Compatibility adapter for the unchanged inspector's native input contract."""
    return PydicomDecoder(decoder).prepare(source, ds, dest)


def read_png(path):
    with Image.open(path) as image:
        return np.array(image), dict(image.info)


def read_pngs(paths):
    """PNG-only reader: accepts no source, sidecar or manifest arguments."""
    frames = [read_png(path) for path in paths]
    if not frames:
        raise ValueError("No PNG frames")
    pixels = np.stack([array for array, _ in frames])
    return {
        "Rows": pixels.shape[1],
        "Columns": pixels.shape[2],
        "frame_count": len(frames),
        "bits": pixels.dtype.itemsize * 8,
        "pixels": pixels,
        "embedded_metadata": [chunks for _, chunks in frames],
    }


@dataclass(frozen=True)
class DisplayTransform:
    low: float
    high: float
    inverted: bool

    @classmethod
    def from_pixels(cls, pixels: np.ndarray, photometric: str) -> "DisplayTransform":
        return cls(float(pixels.min()), float(pixels.max()), photometric == "MONOCHROME1")

    def render(self, pixels: np.ndarray) -> np.ndarray:
        if self.high == self.low:
            shown = np.zeros_like(pixels, dtype=np.uint8)
        else:
            shown = ((pixels.astype(float) - self.low) * 255 / (self.high - self.low)).astype(
                np.uint8
            )
        return 255 - shown if self.inverted else shown

    def reconstruct(self, shown: np.ndarray) -> np.ndarray:
        restored = (255 - shown if self.inverted else shown).astype(float)
        if self.high == self.low:
            return np.full_like(restored, self.low)
        return restored * (self.high - self.low) / 255 + self.low


def frame_array(ds, decoder: str = "native") -> np.ndarray:
    return normalize_frames(ds, decoded(ds, decoder))


def pixel_metrics(pixels: np.ndarray, png: np.ndarray, transform: DisplayTransform) -> dict:
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
