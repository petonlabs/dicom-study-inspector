#!/usr/bin/env python3
"""
ECHOVIEW ↔ MIMIC-IV-ECHO DICOM inspection harness.

Runs locally only. It does not upload or transmit restricted PhysioNet data.

Inputs
------
1) MIMIC_ECHO_View_Classifications.csv       (ECHOVIEW v0.1)
2) echo-record-list.csv                      (MIMIC-IV-ECHO)
3) local DICOM root containing the files referenced by dicom_filepath

Outputs
-------
inspection_cases.csv       sampled cases with ECHOVIEW labels + DICOM linkage
metadata_inventory.csv     flattened non-pixel DICOM metadata for sampled cases
potential_leakage_hits.csv text metadata that may explicitly/implicitly encode view
inspection_summary.json    counts and basic linkage/metadata diagnostics
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import pydicom
from pydicom.dataset import Dataset
from pydicom.sequence import Sequence

ECHOVIEW_PROB_COLUMNS = [
    "prob_plax_far", "prob_plax_plax", "prob_plax_laz",
    "prob_psax_az", "prob_psax_mv", "prob_psax_pap",
    "prob_a2c_lvocc_s", "prob_a2c_laocc", "prob_a2c",
    "prob_a3c_lvocc_s", "prob_a3c_laocc", "prob_a3c",
    "prob_a4c_lvocc_s", "prob_a4c_laocc", "prob_a4c",
    "prob_a5c", "prob_other", "prob_rvinf", "prob_psax_avz",
    "prob_suprasternal", "prob_subcostal", "prob_plax_lac", "prob_psax_apex",
]


def add_prediction_diagnostics(ev: pd.DataFrame, prob_cols: list[str]) -> pd.DataFrame:
    """Add top-2 prediction and uncertainty fields used by the review queue."""
    out = ev.copy()
    probs = out[prob_cols].astype(float)
    order = probs.to_numpy().argsort(axis=1)
    top_idx = order[:, -1]
    second_idx = order[:, -2]
    cols = list(prob_cols)
    out["max_view"] = [cols[i].replace("prob_", "") for i in top_idx]
    out["max_prob"] = probs.to_numpy()[range(len(out)), top_idx]
    out["second_view"] = [cols[i].replace("prob_", "") for i in second_idx]
    out["second_prob"] = probs.to_numpy()[range(len(out)), second_idx]
    out["top2_margin"] = out["max_prob"] - out["second_prob"]
    return out


def merged_value(row: pd.Series, base: str):
    """Read a field robustly after pandas merge suffixing."""
    for key in (base, f"{base}_record", f"{base}_echoview"):
        if key in row.index and pd.notna(row[key]):
            return row[key]
    return ""


VIEW_TERMS = {
    "a4c": [
        r"\ba4c\b", r"apical\s*4", r"apical\s*four", r"4\s*chamber", r"four\s*chamber",
    ],
    "a2c": [
        r"\ba2c\b", r"apical\s*2", r"apical\s*two", r"2\s*chamber", r"two\s*chamber",
    ],
    "a3c": [
        r"\ba3c\b", r"apical\s*3", r"apical\s*three", r"3\s*chamber", r"three\s*chamber",
    ],
    "plax": [
        r"\bplax\b", r"parasternal\s*long", r"long\s*axis",
    ],
    "psax": [
        r"\bpsax\b", r"parasternal\s*short", r"short\s*axis",
    ],
    "subcostal": [
        r"\bsubcostal\b", r"\bsubxiphoid\b",
    ],
    "suprasternal": [
        r"\bsuprasternal\b",
    ],
}
VIEW_REGEX = {
    k: re.compile("|".join(v), flags=re.IGNORECASE) for k, v in VIEW_TERMS.items()
}

# High-yield fields to surface explicitly even when no leakage string is found.
CANDIDATE_KEYWORDS = {
    "StudyDescription",
    "SeriesDescription",
    "ProtocolName",
    "ImageComments",
    "PerformedProcedureStepDescription",
    "RequestedProcedureDescription",
    "ViewCodeSequence",
    "AnatomicRegionSequence",
    "TransducerData",
    "TransducerType",
    "AcquisitionDeviceProcessingDescription",
    "Manufacturer",
    "ManufacturerModelName",
    "StationName",
    "SoftwareVersions",
    "SequenceName",
    "Modality",
    "NumberOfFrames",
    "FrameTime",
    "FrameTimeVector",
    "CineRate",
    "HeartRate",
    "Rows",
    "Columns",
    "PhotometricInterpretation",
}


def safe_str(value: Any) -> str:
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, (list, tuple)):
        return "\\".join(safe_str(x) for x in value)
    try:
        s = str(value)
    except Exception:
        s = repr(value)
    return s[:2000]


def flatten_dataset(ds: Dataset, prefix: str = "", depth: int = 0, max_depth: int = 3):
    """Yield one row per non-pixel element; recurse into sequences."""
    for elem in ds:
        if elem.tag.group == 0x7FE0:  # Pixel Data
            continue
        if elem.tag.is_private:
            continue

        keyword = elem.keyword or elem.name.replace(" ", "")
        path = f"{prefix}.{keyword}" if prefix else keyword

        if elem.VR == "SQ":
            if depth >= max_depth:
                yield {
                    "path": path,
                    "keyword": keyword,
                    "tag": str(elem.tag),
                    "vr": elem.VR,
                    "value": f"<Sequence:{len(elem.value)} items>",
                }
                continue
            for i, item in enumerate(elem.value):
                if isinstance(item, Dataset):
                    yield from flatten_dataset(
                        item,
                        prefix=f"{path}[{i}]",
                        depth=depth + 1,
                        max_depth=max_depth,
                    )
            continue

        yield {
            "path": path,
            "keyword": keyword,
            "tag": str(elem.tag),
            "vr": elem.VR,
            "value": safe_str(elem.value),
        }


def find_leakage(metadata_rows: Iterable[dict], target_view: str):
    rows = []
    target_root = target_view.lower().split("_")[0]
    for r in metadata_rows:
        value = r["value"]
        if not value or value.startswith("<bytes:"):
            continue
        matched_terms = []
        for label, rx in VIEW_REGEX.items():
            if rx.search(value):
                matched_terms.append(label)
        if matched_terms:
            rows.append(
                {
                    **r,
                    "matched_view_terms": ",".join(sorted(set(matched_terms))),
                    "matches_target_family": target_root in matched_terms,
                }
            )
    return rows


def choose_cases(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """
    Deliberately sample:
      - high-confidence cases,
      - ambiguous/low-confidence cases,
      - broad predicted-view coverage.
    """
    if len(df) <= n:
        return df.copy()

    parts = []

    # 40% highest-confidence overall
    n_high = max(1, round(n * 0.4))
    parts.append(df.nlargest(n_high, "max_prob"))

    # 30% lowest-confidence overall
    n_low = max(1, round(n * 0.3))
    parts.append(df.nsmallest(n_low, "max_prob"))

    # Remaining: one-per-view round-robin for diversity
    already = pd.concat(parts)["image"].astype(str).tolist()
    remaining_n = n - len(set(already))
    diverse = (
        df[~df["image"].astype(str).isin(set(already))]
        .sort_values("max_prob", ascending=False)
        .groupby("max_view", as_index=False, group_keys=False)
        .head(1)
        .head(max(0, remaining_n))
    )
    parts.append(diverse)

    out = pd.concat(parts).drop_duplicates("image").head(n)
    if len(out) < n:
        fill = df[~df["image"].isin(out["image"])].sample(
            n=min(n-len(out), len(df)-len(out)), random_state=42
        )
        out = pd.concat([out, fill]).drop_duplicates("image").head(n)
    return out


def resolve_local_path(dicom_root: Path, dicom_filepath: str) -> Path:
    p = Path(str(dicom_filepath))
    candidates = [
        dicom_root / p,
        dicom_root / str(dicom_filepath).lstrip("/"),
        dicom_root / p.name,
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--echoview", required=True, type=Path)
    ap.add_argument("--record-list", required=True, type=Path)
    ap.add_argument("--dicom-root", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    ev = pd.read_csv(args.echoview)
    rec = pd.read_csv(args.record_list)

    # Normalize ECHOVIEW identifier.
    image_col = "Image" if "Image" in ev.columns else "image"
    ev["image"] = ev[image_col].astype(str).str.replace(".dcm", "", regex=False)

    prob_cols = [c for c in ECHOVIEW_PROB_COLUMNS if c in ev.columns]
    missing_prob_cols = [c for c in ECHOVIEW_PROB_COLUMNS if c not in ev.columns]
    unexpected_prob_cols = [c for c in ev.columns if c.startswith("prob_") and c not in ECHOVIEW_PROB_COLUMNS]
    if missing_prob_cols:
        raise ValueError(f"ECHOVIEW schema mismatch; missing probability columns: {missing_prob_cols}")
    if unexpected_prob_cols:
        raise ValueError(f"ECHOVIEW schema mismatch; unexpected probability columns: {unexpected_prob_cols}")

    ev = add_prediction_diagnostics(ev, prob_cols)

    # record-list field names differ slightly by release; match on basename.
    path_col = next(
        (c for c in ["dicom_filepath", "dicom_path", "path"] if c in rec.columns),
        None,
    )
    if path_col is None:
        raise ValueError("Could not find dicom_filepath/path column in record list.")

    rec["image"] = (
        rec[path_col].astype(str).map(lambda x: Path(x).name).str.replace(".dcm", "", regex=False)
    )

    linked = ev.merge(rec, on="image", how="left", indicator=True, suffixes=("_echoview", "_record"))
    linked_ok = linked[linked["_merge"] == "both"].copy()

    cases = choose_cases(linked_ok, args.n)

    case_rows = []
    metadata_rows_all = []
    leakage_rows_all = []

    for _, row in cases.iterrows():
        image = str(row["image"])
        dicom_filepath = str(row[path_col])
        local_path = resolve_local_path(args.dicom_root, dicom_filepath)

        subject_id_echoview = row.get("subject_id_echoview", row.get("subject_id", ""))
        subject_id_record = row.get("subject_id_record", row.get("subject_id", ""))
        study_id_record = row.get("study_id_record", row.get("study_id", ""))
        study_id_from_image = image.split("_")[0]

        case = {
            "image": image,
            "max_view": row["max_view"],
            "max_prob": float(row["max_prob"]),
            "second_view": row["second_view"],
            "second_prob": float(row["second_prob"]),
            "top2_margin": float(row["top2_margin"]),
            "subject_id": merged_value(row, "subject_id"),
            "subject_id_echoview": subject_id_echoview,
            "subject_id_record": subject_id_record,
            "subject_id_link_match": (
                str(subject_id_echoview) == str(subject_id_record)
                if pd.notna(subject_id_echoview) and pd.notna(subject_id_record)
                and str(subject_id_echoview) and str(subject_id_record) else ""
            ),
            "study_id": merged_value(row, "study_id") or study_id_from_image,
            "study_id_record": study_id_record,
            "study_id_from_image": study_id_from_image,
            "study_id_link_match": (
                str(study_id_record) == str(study_id_from_image)
                if pd.notna(study_id_record) and str(study_id_record) else ""
            ),
            "dicom_filepath": dicom_filepath,
            "local_path": str(local_path),
            "file_exists": local_path.exists(),
            "dicom_read_ok": False,
            "number_of_frames": "",
            "manufacturer": "",
            "manufacturer_model": "",
            "series_description": "",
            "protocol_name": "",
            "explicit_or_implicit_view_text_found": False,
            "target_family_match_found": False,
            "manual_visual_check": "",      # fill in Inspector
            "manual_notes": "",             # fill in Inspector
        }

        if not local_path.exists():
            case_rows.append(case)
            continue

        try:
            ds = pydicom.dcmread(local_path, stop_before_pixels=True, force=True)
            case["dicom_read_ok"] = True
            case["number_of_frames"] = safe_str(getattr(ds, "NumberOfFrames", ""))
            case["manufacturer"] = safe_str(getattr(ds, "Manufacturer", ""))
            case["manufacturer_model"] = safe_str(getattr(ds, "ManufacturerModelName", ""))
            case["series_description"] = safe_str(getattr(ds, "SeriesDescription", ""))
            case["protocol_name"] = safe_str(getattr(ds, "ProtocolName", ""))

            flattened = list(flatten_dataset(ds))
            for m in flattened:
                m2 = {
                    "image": image,
                    "max_view": row["max_view"],
                    "max_prob": float(row["max_prob"]),
                    "second_view": row["second_view"],
                    "second_prob": float(row["second_prob"]),
                    "top2_margin": float(row["top2_margin"]),
                    **m,
                    "is_candidate_field": m["keyword"] in CANDIDATE_KEYWORDS,
                }
                metadata_rows_all.append(m2)

            leaks = find_leakage(flattened, str(row["max_view"]))
            for leak in leaks:
                leakage_rows_all.append(
                    {
                        "image": image,
                        "max_view": row["max_view"],
                        "max_prob": float(row["max_prob"]),
                        "second_view": row["second_view"],
                        "second_prob": float(row["second_prob"]),
                        "top2_margin": float(row["top2_margin"]),
                        **leak,
                    }
                )
            case["explicit_or_implicit_view_text_found"] = bool(leaks)
            case["target_family_match_found"] = any(x["matches_target_family"] for x in leaks)

        except Exception as e:
            case["dicom_error"] = repr(e)

        case_rows.append(case)

    cases_df = pd.DataFrame(case_rows)
    metadata_df = pd.DataFrame(metadata_rows_all)
    leakage_df = pd.DataFrame(leakage_rows_all)

    cases_df.to_csv(args.out / "inspection_cases.csv", index=False)
    metadata_df.to_csv(args.out / "metadata_inventory.csv", index=False)
    leakage_df.to_csv(args.out / "potential_leakage_hits.csv", index=False)

    summary = {
        "echoview_rows": int(len(ev)),
        "record_list_rows": int(len(rec)),
        "linked_rows": int(len(linked_ok)),
        "unlinked_echoview_rows": int((linked["_merge"] != "both").sum()),
        "sampled_cases": int(len(cases_df)),
        "echoview_probability_columns": int(len(prob_cols)),
        "median_top2_margin_sample": float(cases_df["top2_margin"].median()) if len(cases_df) else None,
        "sample_files_present": int(cases_df["file_exists"].sum()) if len(cases_df) else 0,
        "sample_dicoms_read_ok": int(cases_df["dicom_read_ok"].sum()) if len(cases_df) else 0,
        "subject_link_mismatches": int((cases_df["subject_id_link_match"] == False).sum()) if len(cases_df) else 0,
        "study_link_mismatches": int((cases_df["study_id_link_match"] == False).sum()) if len(cases_df) else 0,
        "cases_with_view_text_hit": int(
            cases_df["explicit_or_implicit_view_text_found"].sum()
        ) if len(cases_df) else 0,
        "cases_with_target_family_text_hit": int(
            cases_df["target_family_match_found"].sum()
        ) if len(cases_df) else 0,
        "note": (
            "Text hits are screening signals, not proof of leakage. "
            "Review each hit in the Study Inspector and distinguish explicit target labels, "
            "workflow/acquisition cues, generic anatomy terms, and harmless metadata."
        ),
    }
    (args.out / "inspection_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    print(json.dumps(summary, indent=2))
    print(f"\nOutputs written to: {args.out.resolve()}")


if __name__ == "__main__":
    main()
