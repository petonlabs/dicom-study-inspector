"""Pure condition-level metadata recovery and pixel measurement assembly."""

from pydicom.datadict import tag_for_keyword

from .config import RECOVERY_FIELDS
from .metadata import eligible, exact_value, flatten_json, present_values, retention
from .pixels import pixel_metrics


def recovered_conditions(rows, png, sidecar, original_syntax):
    recovered = present_values(flatten_json(sidecar["raw_inventory"]))
    recovered["file_meta.00020010"] = [original_syntax]
    return {
        "dicom": present_values(rows),
        "png_only": {"00280010": [png["Rows"]], "00280011": [png["Columns"]]},
        "png_sidecar": recovered,
    }


def matched_inventory(name, rows, conditions):
    return [{"instance": name, **row, **row_matches(row, conditions)} for row in rows]


def row_matches(row, conditions):
    return {
        label + "_exact": exact_value(row["path"], row["value"], conditions[label])
        if eligible(row)
        else None
        for label in ("png_only", "png_sidecar")
    }


def linkage_recovery(native, recovered):
    result = {}
    for keyword in RECOVERY_FIELDS:
        path = f"{tag_for_keyword(keyword):08X}"
        result[keyword + "_recoverable"] = (
            exact_value(path, native[path], recovered) if path in native else None
        )
    return result


def frame_metrics(instance, exported):
    return {
        "decoded_frames": len(exported.pixels),
        "export_frames": exported.png["frame_count"],
        "source_bits_stored": int(instance.dataset.BitsStored),
        "export_bits": exported.png["bits"],
        **pixel_metrics(exported.pixels, exported.png["pixels"], exported.transform),
    }


def condition_metrics(label, metrics):
    if label == "png_sidecar":
        return metrics.copy()
    return {
        **metrics,
        "reconstruction_mae_stored_units": None,
        "reconstruction_max_error_stored_units": None,
    }


def measurements(instance, exported, conditions):
    metrics = frame_metrics(instance, exported)
    records = []
    for label, fields in conditions.items():
        records.append(
            {
                "instance": instance.entry["id"],
                "modality": str(instance.dataset.get("Modality", "")),
                "condition": label,
                **retention(instance.inventory, fields),
                **condition_metrics(label, metrics),
                **linkage_recovery(conditions["dicom"], fields),
            }
        )
    return records
