"""Pure metadata inventory, representation validation, and retention measurements."""

import pydicom
from pydicom.datadict import tag_for_keyword
from pydicom.valuerep import validate_value

from .config import COMMON, GROUPS, PIXEL_TAGS


def empty_value(value):
    if isinstance(value, dict):
        return all(empty_value(v) for v in value.values())
    if isinstance(value, list):
        return all(empty_value(v) for v in value)
    return value is None or value == ""


def flatten_json(data, prefix=""):
    """Canonical DICOM JSON leaves; do not count SQ containers or binary payloads."""
    rows = []
    for tag, element in sorted(data.items()):
        if tag in PIXEL_TAGS:
            continue
        path = f"{prefix}{tag}"
        vr = element["vr"]
        keyword = pydicom.datadict.keyword_for_tag(int(tag, 16)) or tag
        row = {
            "path": path,
            "tag": tag,
            "keyword": keyword,
            "vr": vr,
            "private": bool(int(tag[:4], 16) % 2),
            "value": element.get("Value"),
            "presence": "empty" if empty_value(element.get("Value")) else "present",
            "validation": [],
            "countable": vr != "SQ",
        }
        if vr == "SQ":
            rows.append(row)
            for i, item in enumerate(element.get("Value", [])):
                rows.extend(flatten_json(item, f"{path}[{i}]."))
        else:
            if "BulkDataURI" in element or "InlineBinary" in element:
                row.update(presence="present", value=None, countable=False)
                row["validation"] = ["binary_value_omitted"]
            rows.append(row)
    return rows


def inventory(ds):
    data = ds.to_json_dict(
        bulk_data_threshold=0, bulk_data_element_handler=lambda e: "omitted:binary"
    )
    rows = flatten_json(data)
    rows.extend(flatten_json(ds.file_meta.to_json_dict(), "file_meta."))
    focus = COMMON | set(GROUPS.get(str(ds.get("Modality", "")), "").split())
    found = {r["keyword"] for r in rows}
    for keyword in sorted(focus - found):
        tag = tag_for_keyword(keyword)
        rows.append(
            {
                "path": f"{tag:08X}",
                "tag": f"{tag:08X}",
                "keyword": keyword,
                "vr": pydicom.datadict.dictionary_VR(tag),
                "private": False,
                "value": None,
                "presence": "missing",
                "validation": [],
                "countable": False,
            }
        )

    # Validate decoded representations without changing their independent presence status.
    def validate(dataset, prefix=""):
        errors = {}
        for e in dataset:
            path = f"{prefix}{int(e.tag):08X}"
            if e.VR == "SQ":
                for i, item in enumerate(e.value):
                    errors.update(validate(item, f"{path}[{i}]."))
            elif e.value is not None and e.VR not in (
                "OB",
                "OW",
                "UN",
                "OF",
                "OD",
                "OL",
                "OV",
            ):
                values = (
                    e.value
                    if isinstance(e.value, (pydicom.multival.MultiValue, list, tuple))
                    else [e.value]
                )
                for value in values:
                    if value is None or value == "":
                        continue
                    try:
                        # DS/IS validators require encoded text, whereas pydicom parses
                        # them into numeric wrappers; validate their preserved text.
                        check = (
                            getattr(value, "original_string", str(value))
                            if e.VR in ("DS", "IS")
                            else value
                        )
                        validate_value(e.VR, check, pydicom.config.RAISE)
                    except (ValueError, TypeError):
                        errors.setdefault(path, []).append("invalid_vr_value")
        return errors

    errors = validate(ds)
    for r in rows:
        r["focused"] = r["keyword"] in focus
        r["validation"].extend(errors.get(r["path"], []))
    return rows


def retention(rows, recovered):
    counts = {}
    for name, selected in [
        ("all", rows),
        ("focused", [r for r in rows if r["focused"]]),
    ]:
        eligible = [
            r for r in selected if r["countable"] and r["presence"] == "present"
        ]
        matched = sum(
            r["path"] in recovered and recovered[r["path"]] == r["value"]
            for r in eligible
        )
        counts[name + "_eligible"] = len(eligible)
        counts[name + "_matched"] = matched
        counts[name + "_fraction"] = matched / len(eligible) if eligible else None
    return counts
