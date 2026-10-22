"""Strict report parsing and reconciliation primitives; no production calculations."""

import csv
import json
import math


def read_table(path):
    previous = csv.field_size_limit()
    try:
        csv.field_size_limit(max(previous, path.stat().st_size))
        with path.open(newline="") as stream:
            return parsed_table(csv.DictReader(stream))
    finally:
        csv.field_size_limit(previous)


def parsed_table(reader):
    if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ValueError("Scientific audit: missing or duplicate CSV columns")
    rows = list(reader)
    if any(None in row for row in rows):
        raise ValueError("Scientific audit: malformed CSV record")
    return rows


def indexed(records, keys):
    result = {}
    for record in records:
        key = tuple(record[field] for field in keys)
        if key in result:
            raise ValueError("Scientific audit: duplicate record")
        result[key] = record
    return result


def csv_value(value, expected):
    if expected is None:
        return None if value == "" else value
    if isinstance(expected, bool):
        return {"True": True, "False": False}.get(value, value)
    if isinstance(expected, (list, dict)):
        return json.loads(value)
    if isinstance(expected, int):
        return int(value)
    if isinstance(expected, float):
        return float(value)
    return value


def equal_value(actual, expected):
    if isinstance(expected, float):
        return (
            type(actual) in (int, float)
            and math.isfinite(actual)
            and math.isfinite(expected)
            and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)
        )
    return type(actual) is type(expected) and actual == expected


def check_fields(actual, expected, label, csv=False):
    for field, value in expected.items():
        if field not in actual:
            raise ValueError(f"Scientific audit: {label}: missing {field}")
        try:
            observed = csv_value(actual[field], value) if csv else actual[field]
        except (TypeError, ValueError):
            raise ValueError(f"Scientific audit: {label}: invalid {field}") from None
        if not equal_value(observed, value):
            raise ValueError(f"Scientific audit: {label}: inconsistent {field}")


def check_table(actual, expected, keys, label):
    observed, reference = indexed(actual, keys), indexed(expected, keys)
    if observed.keys() != reference.keys():
        raise ValueError(f"Scientific audit: {label}: missing or unexpected records")
    for key, expected_row in reference.items():
        check_fields(observed[key], expected_row, label, csv=True)


def check_report(path, summary):
    text = path.read_text()
    if text.count("```json\n") != 1:
        raise ValueError("Scientific audit: missing or ambiguous report summary")
    embedded = json.loads(text.split("```json\n", 1)[1].split("```", 1)[0])
    if embedded.keys() != summary.keys():
        raise ValueError("Scientific audit: report summary fields changed")
    check_fields(embedded, summary, "report")
