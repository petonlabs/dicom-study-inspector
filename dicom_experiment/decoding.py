"""Bounded local decoding and lossless native-input adaptation."""

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import pydicom
from pydicom.dataset import Dataset

from .config import LOSSLESS_SYNTAXES, MAX_DECODED_SAMPLES


class Decoder(Protocol):
    def decode(self, dataset: Dataset) -> np.ndarray: ...
    def prepare(self, source: Path, dataset: Dataset, destination: Path) -> Path: ...


def require_pixel_bounds(ds):
    dimensions = [
        int(ds.get(k, 1)) for k in ("NumberOfFrames", "Rows", "Columns", "SamplesPerPixel")
    ]
    count = 1
    for dimension in dimensions:
        count *= dimension
    if min(dimensions) < 1 or count > MAX_DECODED_SAMPLES:
        raise ValueError("Decoded sample limit exceeded or invalid dimensions")


def require_lossless(ds, decoder):
    if decoder != "gdcm" or str(ds.file_meta.TransferSyntaxUID) not in LOSSLESS_SYNTAXES:
        raise ValueError("Unsupported compressed syntax for experiment")


@dataclass(frozen=True)
class PydicomDecoder:
    name: str = "native"

    def decode(self, dataset):
        require_pixel_bounds(dataset)
        if dataset.file_meta.TransferSyntaxUID.is_compressed:
            require_lossless(dataset, self.name)
            return pydicom.pixels.pixel_array(dataset, decoding_plugin="gdcm")
        return dataset.pixel_array

    def prepare(self, source, dataset, destination):
        if not dataset.file_meta.TransferSyntaxUID.is_compressed:
            return source
        pixels = self.decode(dataset)
        native = copy.deepcopy(dataset)
        native.decompress(decoding_plugin="gdcm", generate_instance_uid=False)
        if not np.array_equal(native.pixel_array, pixels):
            raise ValueError("Decompression changed decoded pixels")
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / "decoded-native.dcm"
        native.save_as(path, enforce_file_format=True)
        return path


def normalize_frames(ds, pixels):
    return pixels[np.newaxis, ...] if int(ds.get("NumberOfFrames", 1)) == 1 else pixels


def select_decoder(name, override=None):
    """Resolve the built-in policy only when the caller has not supplied an adapter."""
    return override if override is not None else PydicomDecoder(name)
