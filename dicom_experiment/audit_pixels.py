"""Direct pixel measurements independent of DisplayTransform and pixel_metrics."""

import numpy as np
from PIL import Image

from .artifacts import inside


def load_png_pixels(root, frames):
    arrays = []
    for frame in frames:
        with Image.open(inside(root, frame["path"])) as image:
            arrays.append(np.asarray(image).copy())
    if not arrays:
        raise ValueError("Scientific audit: no PNG frames")
    return np.stack(arrays)


def forward_pixels(source, inverted):
    minimum, maximum = float(np.min(source)), float(np.max(source))
    if minimum == maximum:
        scaled = np.zeros(source.shape, dtype=np.uint8)
    else:
        scaled = np.asarray(
            (source.astype(np.float64) - minimum) * 255 / (maximum - minimum), dtype=np.uint8
        )
    return np.subtract(255, scaled, dtype=np.uint8) if inverted else scaled


def reconstruction_errors(source, png, inverted):
    minimum, maximum = float(source.min()), float(source.max())
    codes = (255 - png if inverted else png).astype(np.float64)
    reconstructed = codes * (maximum - minimum) / 255 + minimum
    difference = np.abs(np.subtract(reconstructed, source.astype(np.float64)))
    return {
        "reconstruction_mae_stored_units": float(np.mean(difference)),
        "reconstruction_max_error_stored_units": float(np.max(difference)),
    }


def intensity_metrics(source, png, inverted):
    source_levels, png_levels = np.unique(source).size, np.unique(png).size
    return {
        "dimensions_preserved": source.shape == png.shape,
        "source_unique_values": int(source_levels),
        "export_unique_values": int(png_levels),
        "unique_value_collapse_fraction": 1 - png_levels / source_levels,
        "png_matches_transform": bool(np.array_equal(png, forward_pixels(source, inverted))),
        **reconstruction_errors(source, png, inverted),
    }


def pixel_evidence(dataset, source, png):
    framed = (
        source.reshape((1,) + source.shape)
        if int(dataset.get("NumberOfFrames", 1)) == 1
        else source
    )
    inverted = str(dataset.PhotometricInterpretation) == "MONOCHROME1"
    return {
        "decoded_frames": len(framed),
        "export_frames": len(png),
        "source_bits_stored": int(dataset.BitsStored),
        "export_bits": png.dtype.itemsize * 8,
        **intensity_metrics(framed, png, inverted),
    }
