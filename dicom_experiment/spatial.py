"""Sample-only spatial compatibility and physical ordering."""

import numpy as np


def uid(ds, key):
    return str(ds.get(key, "")).strip() or None


def require_single_frame_reference(items):
    if any(int(ds.get("NumberOfFrames", 1)) != 1 for _, ds in items):
        raise ValueError("multiframe_geometry_not_resolved")
    refs = {uid(ds, "FrameOfReferenceUID") for _, ds in items}
    if None in refs or len(refs) != 1:
        raise ValueError("missing_or_incompatible_frame_of_reference")


def require_direction_cosines(orientation):
    if orientation.shape != (6,) or not np.isfinite(orientation).all():
        raise ValueError("invalid_orientation")
    row, column = orientation[:3], orientation[3:]
    if not (
        np.isclose(np.linalg.norm(row), 1, atol=1e-4)
        and np.isclose(np.linalg.norm(column), 1, atol=1e-4)
        and np.isclose(np.dot(row, column), 0, atol=1e-4)
    ):
        raise ValueError("invalid_direction_cosines")


def compatible_normal(items):
    orientations = [np.asarray(ds.ImageOrientationPatient, dtype=float) for _, ds in items]
    first = orientations[0]
    require_direction_cosines(first)
    for orientation in orientations:
        if orientation.shape != (6,) or not np.allclose(orientation, first, atol=1e-4, rtol=0):
            raise ValueError("incompatible_orientation")
    return np.cross(first[:3], first[3:])


def project_position(ds, normal):
    position = np.asarray(ds.ImagePositionPatient, dtype=float)
    if position.shape != (3,) or not np.isfinite(position).all():
        raise ValueError("invalid_position")
    return float(np.dot(position, normal))


def ordered_positions(items):
    require_single_frame_reference(items)
    normal = compatible_normal(items)
    projected = sorted((project_position(ds, normal), name) for name, ds in items)
    gaps = np.diff([position for position, _ in projected]).tolist()
    return {
        "status": "ambiguous_duplicate_positions"
        if any(abs(g) < 1e-6 for g in gaps)
        else "ordered",
        "order": [name for _, name in projected],
        "sample_gaps_mm": gaps,
        "scope": "sample only; gaps do not establish missing slices or full-series spacing",
    }


def spatial_order(items):
    if len(items) < 2:
        return {"status": "insufficient_sample", "order": [], "sample_gaps_mm": []}
    try:
        return ordered_positions(items)
    except (AttributeError, TypeError, ValueError) as exc:
        return {"status": "unresolved", "reason": str(exc), "order": [], "sample_gaps_mm": []}
