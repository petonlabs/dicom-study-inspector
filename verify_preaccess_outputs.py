#!/usr/bin/env python3
"""Smoke checks for the synthetic pre-access inspection run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    cases = pd.read_csv(args.out / "inspection_cases.csv")
    summary = json.loads((args.out / "inspection_summary.json").read_text())

    required = {
        "image", "subject_id", "study_id", "max_view", "max_prob",
        "second_view", "second_prob", "top2_margin", "dicom_read_ok",
        "manual_visual_check", "manual_notes",
    }
    missing = sorted(required - set(cases.columns))
    assert not missing, f"missing expected case columns: {missing}"
    assert summary["echoview_probability_columns"] == 23
    assert summary["sampled_cases"] > 0
    assert summary["sample_files_present"] == summary["sampled_cases"]
    assert summary["sample_dicoms_read_ok"] == summary["sampled_cases"]
    assert cases["subject_id"].notna().all(), "subject_id lost during merge"
    assert cases["study_id"].notna().all(), "study_id lost during merge"
    assert (cases["top2_margin"] >= 0).all()
    assert summary["cases_with_view_text_hit"] > 0, "fixture should trigger leakage screening"

    print("PASS: pre-access inspection contract is internally coherent")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
