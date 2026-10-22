"""Independent DICOM/sidecar traversal; does not use production inventory or counters."""

from pydicom.datadict import dictionary_VR, keyword_for_tag, tag_for_keyword
from pydicom.valuerep import validate_value

from .config import COMMON, GROUPS, PIXEL_TAGS, RECOVERY_FIELDS

BINARY = {"OB", "OW", "UN", "OF", "OD", "OL", "OV"}


def populated(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif item is not None and item != "":
            return True
    return False


def binary_marker(element):
    return "omitted:binary"


def element_validation(element):
    if element.is_empty or element.VR == "SQ" or element.VR in BINARY:
        return []
    values = list(element.value) if element.VM > 1 else [element.value]
    return [
        "invalid_vr_value"
        for value in values
        if value is not None and value != "" and not representation_valid(element.VR, value)
    ]


def representation_valid(vr, value):
    candidate = getattr(value, "original_string", str(value)) if vr in {"DS", "IS"} else value
    try:
        validate_value(vr, candidate, 2)
        return True
    except (ValueError, TypeError):
        return False


def evidence_row(path, tag, encoded):
    omitted = "InlineBinary" in encoded or "BulkDataURI" in encoded
    value = None if omitted else encoded.get("Value")
    return {
        "path": path,
        "tag": tag,
        "keyword": keyword_for_tag(int(tag, 16)) or tag,
        "vr": encoded["vr"],
        "private": bool(int(tag[:4], 16) & 1),
        "value": value,
        "presence": "present" if omitted or populated(value) else "empty",
        "countable": encoded["vr"] != "SQ" and not omitted,
        "validation": ["binary_value_omitted"] if omitted else [],
    }


def dataset_rows(dataset, prefix=""):
    rows, pending = [], [(prefix, dataset)]
    while pending:
        base, current = pending.pop()
        for element in current:
            tag = f"{int(element.tag):08X}"
            if tag in PIXEL_TAGS:
                continue
            row = evidence_row(base + tag, tag, element.to_json_dict(binary_marker, 0))
            row["validation"] += [] if prefix else element_validation(element)
            rows.append(row)
            if element.VR == "SQ":
                pending.extend((f"{base}{tag}[{i}].", item) for i, item in enumerate(element.value))
    return rows


def absent_field(keyword):
    tag = f"{tag_for_keyword(keyword):08X}"
    row = evidence_row(tag, tag, {"vr": dictionary_VR(int(tag, 16))})
    return {**row, "presence": "missing", "countable": False}


def source_inventory(dataset):
    rows = dataset_rows(dataset) + dataset_rows(dataset.file_meta, "file_meta.")
    focus = COMMON.union(GROUPS.get(str(dataset.get("Modality", "")), "").split())
    missing = focus.difference(row["keyword"] for row in rows)
    rows.extend(absent_field(keyword) for keyword in sorted(missing))
    return {row["path"]: {**row, "focused": row["keyword"] in focus} for row in rows}


def sidecar_values(raw):
    values, pending = {}, [("", raw)]
    while pending:
        prefix, elements = pending.pop()
        for tag, encoded in elements.items():
            path = prefix + tag
            if tag in PIXEL_TAGS:
                continue
            if encoded["vr"] == "SQ":
                pending.extend(child_sequences(path, encoded))
            elif "InlineBinary" not in encoded and "BulkDataURI" not in encoded:
                if populated(encoded.get("Value")):
                    values[path] = encoded["Value"]
    return values


def child_sequences(path, encoded):
    return [(f"{path}[{index}].", child) for index, child in enumerate(encoded.get("Value", []))]


def eligible_paths(rows, focused=False):
    return {
        path
        for path, row in rows.items()
        if row["countable"] and row["presence"] == "present" and (not focused or row["focused"])
    }


def representation_values(rows, png, sidecar, syntax):
    native = {path: rows[path]["value"] for path in eligible_paths(rows)}
    side = sidecar_values(sidecar["raw_inventory"])
    side["file_meta.00020010"] = [syntax]
    return {
        "dicom": native,
        "png_only": {"00280010": [png.shape[1]], "00280011": [png.shape[2]]},
        "png_sidecar": side,
    }


def exact_paths(rows, recovered):
    return {
        path
        for path in eligible_paths(rows)
        if path in recovered and rows[path]["value"] == recovered[path]
    }


def count_retention(rows, recovered):
    matched = exact_paths(rows, recovered)
    counts = {}
    for label, focused in (("all", False), ("focused", True)):
        eligible = eligible_paths(rows, focused)
        numerator, denominator = len(matched & eligible), len(eligible)
        counts.update(
            {
                label + "_eligible": denominator,
                label + "_matched": numerator,
                label + "_fraction": numerator / denominator if denominator else None,
            }
        )
    return counts


def recovery_flags(rows, recovered):
    matched, eligible = exact_paths(rows, recovered), eligible_paths(rows)
    result = {}
    for keyword in RECOVERY_FIELDS:
        path = f"{tag_for_keyword(keyword):08X}"
        result[keyword + "_recoverable"] = path in matched if path in eligible else None
    return result


def inventory_expectations(name, rows, representations=None):
    expected = []
    for path, row in rows.items():
        record = {"instance": name, **row, "png_only_exact": None, "png_sidecar_exact": None}
        if representations is not None:
            for label in ("png_only", "png_sidecar"):
                record[label + "_exact"] = (
                    path in exact_paths(rows, representations[label])
                    if path in eligible_paths(rows)
                    else None
                )
        expected.append(record)
    return expected
