"""Pure metadata inventory, value validation and exact retention calculations."""

import pydicom
from pydicom.datadict import dictionary_VR, keyword_for_tag, tag_for_keyword
from pydicom.valuerep import validate_value

from .config import COMMON, GROUPS, PIXEL_TAGS

BINARY_VRS = {"OB", "OW", "UN", "OF", "OD", "OL", "OV"}


def empty_value(value):
    if isinstance(value, dict):
        return all(empty_value(v) for v in value.values())
    if isinstance(value, list):
        return all(empty_value(v) for v in value)
    return value is None or value == ""


def eligible(row):
    return row["countable"] and row["presence"] == "present"


def exact_value(path, value, recovered):
    return path in recovered and recovered[path] == value


def present_values(rows):
    return {r["path"]: r["value"] for r in rows if eligible(r)}


def inventory_row(tag, element, prefix):
    return {
        "path": f"{prefix}{tag}",
        "tag": tag,
        "keyword": keyword_for_tag(int(tag, 16)) or tag,
        "vr": element["vr"],
        "private": bool(int(tag[:4], 16) % 2),
        "value": element.get("Value"),
        "presence": "empty" if empty_value(element.get("Value")) else "present",
        "validation": [],
        "countable": element["vr"] != "SQ",
    }


def sequence_items(items, path):
    for index, item in enumerate(items):
        yield f"{path}[{index}].", item


def element_rows(tag, element, prefix):
    row = inventory_row(tag, element, prefix)
    if "BulkDataURI" in element or "InlineBinary" in element:
        row.update(presence="present", value=None, countable=False)
        row["validation"] = ["binary_value_omitted"]
    yield row
    if element["vr"] == "SQ":
        for child_prefix, item in sequence_items(element.get("Value", []), row["path"]):
            yield from flatten_json(item, child_prefix)


def flatten_json(data, prefix=""):
    """Canonical leaves: retain sequence paths but exclude binary values from counts."""
    rows = []
    for tag, element in sorted(data.items()):
        if tag not in PIXEL_TAGS:
            rows.extend(element_rows(tag, element, prefix))
    return rows


def missing_row(keyword):
    tag = tag_for_keyword(keyword)
    row = inventory_row(f"{tag:08X}", {"vr": dictionary_VR(tag)}, "")
    return {**row, "presence": "missing", "countable": False}


def decoded_values(element):
    if isinstance(element.value, (pydicom.multival.MultiValue, list, tuple)):
        return element.value
    return [element.value]


def valid_value(vr, value):
    if value is None or value == "":
        return True
    check = getattr(value, "original_string", str(value)) if vr in ("DS", "IS") else value
    try:
        validate_value(vr, check, pydicom.config.RAISE)
        return True
    except (ValueError, TypeError):
        return False


def validation_errors(dataset, prefix=""):
    errors = {}
    for element in dataset:
        path = f"{prefix}{int(element.tag):08X}"
        if element.VR == "SQ":
            for child_prefix, item in sequence_items(element.value, path):
                errors.update(validation_errors(item, child_prefix))
        elif element.VR not in BINARY_VRS:
            for value in decoded_values(element):
                if not valid_value(element.VR, value):
                    errors.setdefault(path, []).append("invalid_vr_value")
    return errors


def omitted_binary(element):
    return "omitted:binary"


def inventory(ds):
    data = ds.to_json_dict(bulk_data_threshold=0, bulk_data_element_handler=omitted_binary)
    rows = flatten_json(data) + flatten_json(ds.file_meta.to_json_dict(), "file_meta.")
    focus = COMMON | set(GROUPS.get(str(ds.get("Modality", "")), "").split())
    missing = focus - {r["keyword"] for r in rows}
    rows.extend(missing_row(keyword) for keyword in sorted(missing))
    errors = validation_errors(ds)
    return [annotated_row(row, focus, errors) for row in rows]


def annotated_row(row, focus, errors):
    return {
        **row,
        "focused": row["keyword"] in focus,
        "validation": row["validation"] + errors.get(row["path"], []),
    }


def retention_counts(name, rows, recovered):
    selected = [r for r in rows if eligible(r)]
    matched = sum(exact_value(r["path"], r["value"], recovered) for r in selected)
    return {
        name + "_eligible": len(selected),
        name + "_matched": matched,
        name + "_fraction": matched / len(selected) if selected else None,
    }


def retention(rows, recovered):
    focused = [r for r in rows if r["focused"]]
    return {
        **retention_counts("all", rows, recovered),
        **retention_counts("focused", focused, recovered),
    }
