#!/usr/bin/env python3
"""Create a deterministic synthetic ECHOVIEW + record-list + DICOM fixture.

Purpose: exercise the exact pre-training inspection contract before restricted
PhysioNet bytes are available. No real patient data are used.
"""
from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

PROB_COLS = [
    "prob_plax_far", "prob_plax_plax", "prob_plax_laz",
    "prob_psax_az", "prob_psax_mv", "prob_psax_pap",
    "prob_a2c_lvocc_s", "prob_a2c_laocc", "prob_a2c",
    "prob_a3c_lvocc_s", "prob_a3c_laocc", "prob_a3c",
    "prob_a4c_lvocc_s", "prob_a4c_laocc", "prob_a4c",
    "prob_a5c", "prob_other", "prob_rvinf", "prob_psax_avz",
    "prob_suprasternal", "prob_subcostal", "prob_plax_lac", "prob_psax_apex",
]

VIEW_TEXT = {
    "plax_far": "Parasternal long axis remote",
    "plax_plax": "Parasternal long axis",
    "plax_laz": "Parasternal long axis left atrium zoom",
    "psax_az": "Parasternal short axis aortic valve",
    "psax_mv": "Parasternal short axis mitral valve",
    "psax_pap": "Parasternal short axis papillary muscle",
    "a2c_lvocc_s": "Apical 2 chamber LV occluded",
    "a2c_laocc": "Apical 2 chamber LA occluded",
    "a2c": "Apical 2 chamber",
    "a3c_lvocc_s": "Apical 3 chamber LV occluded",
    "a3c_laocc": "Apical 3 chamber LA occluded",
    "a3c": "Apical 3 chamber",
    "a4c_lvocc_s": "Apical 4 chamber LV occluded",
    "a4c_laocc": "Apical 4 chamber LA occluded",
    "a4c": "Apical 4 chamber",
    "a5c": "Apical 5 chamber",
    "other": "Contrast Doppler",
    "rvinf": "RV inflow",
    "psax_avz": "Parasternal short axis aortic valve zoom",
    "suprasternal": "Suprasternal",
    "subcostal": "Subcostal",
    "plax_lac": "Parasternal long axis left atrium centered",
    "psax_apex": "Parasternal short axis apex",
}


def probability_row(primary: str, secondary: str, peak: float) -> dict[str, float]:
    n = len(PROB_COLS)
    second = max(0.02, min(peak - 0.01, (1.0 - peak) * 0.58))
    remainder = max(0.0, 1.0 - peak - second)
    base = remainder / (n - 2)
    row = {c: base for c in PROB_COLS}
    row[f"prob_{primary}"] = peak
    row[f"prob_{secondary}"] = second
    # Preserve a probability simplex after floating-point rounding.
    total = sum(row.values())
    return {k: v / total for k, v in row.items()}


def write_dicom(path: Path, *, subject_id: int, study_id: int, idx: int,
                primary: str, metadata_mode: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sop_class_uid = "1.2.840.10008.5.1.4.1.1.3.1"  # Ultrasound Multi-frame Image Storage
    sop_instance_uid = generate_uid()

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = sop_class_uid
    meta.MediaStorageSOPInstanceUID = sop_instance_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = generate_uid()

    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = sop_class_uid
    ds.SOPInstanceUID = sop_instance_uid
    ds.PatientID = f"SYNTH-{subject_id}"
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.Modality = "US"
    ds.NumberOfFrames = str(30 + (idx % 45))
    ds.Manufacturer = "Synthetic Ultrasound Co"
    ds.ManufacturerModelName = "PREACCESS-1"
    ds.Rows = 112
    ds.Columns = 112
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.CineRate = 30
    ds.HeartRate = 60 + (idx % 25)

    if metadata_mode == "explicit":
        ds.SeriesDescription = VIEW_TEXT[primary]
        ds.ProtocolName = "Routine TTE"
    elif metadata_mode == "workflow":
        # Deliberately weaker cue: useful for testing the distinction between
        # explicit target leakage and acquisition/workflow correlation.
        root = primary.split("_")[0]
        ds.SeriesDescription = f"Echo acquisition {root.upper()} sequence"
        ds.ProtocolName = "Routine TTE"
    else:
        ds.SeriesDescription = "Routine transthoracic echo cine"
        ds.ProtocolName = "Adult echocardiography"

    ds.save_as(path, enforce_file_format=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("review/preaccess_fixture"))
    ap.add_argument("--n", type=int, default=36)
    args = ap.parse_args()

    rng = random.Random(42)
    out = args.out
    dicom_root = out / "dicom"
    out.mkdir(parents=True, exist_ok=True)

    ev_rows = []
    rec_rows = []
    classes = [c.replace("prob_", "") for c in PROB_COLS]
    peaks = [0.97, 0.92, 0.84, 0.74, 0.61, 0.44]

    for i in range(args.n):
        study_id = 81000000 + (i // 6)
        subject_id = 10000000 + (i // 6)
        view_no = i % 10000
        image = f"{study_id:08d}_{view_no:04d}"
        primary = classes[i % len(classes)]
        secondary = classes[(i + 5) % len(classes)]
        peak = peaks[i % len(peaks)]
        probs = probability_row(primary, secondary, peak)

        # Mimic ECHOVIEW's 25-column shape: Image + subject_id + 23 probabilities.
        ev_rows.append({"Image": image, "subject_id": subject_id, **probs})

        rel = Path(f"p{str(subject_id)[:2]}") / f"p{subject_id}" / f"s{study_id}" / f"{image}.dcm"
        rec_rows.append({
            "subject_id": subject_id,
            "study_id": study_id,
            "dicom_filepath": str(rel),
        })

        mode = "explicit" if i % 4 == 0 else "workflow" if i % 5 == 0 else "benign"
        write_dicom(
            dicom_root / rel,
            subject_id=subject_id,
            study_id=study_id,
            idx=i,
            primary=primary,
            metadata_mode=mode,
        )

    with (out / "MIMIC_ECHO_View_Classifications.synthetic.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Image", "subject_id", *PROB_COLS])
        w.writeheader()
        w.writerows(ev_rows)

    with (out / "echo-record-list.synthetic.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["subject_id", "study_id", "dicom_filepath"])
        w.writeheader()
        w.writerows(rec_rows)

    print(f"Synthetic ECHOVIEW rows: {len(ev_rows)}")
    print(f"Synthetic DICOM files: {len(rec_rows)}")
    print(f"Fixture root: {out.resolve()}")


if __name__ == "__main__":
    main()
