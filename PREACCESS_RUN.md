# ECHOVIEW pre-access contract test

This keeps the real experiment intact while PhysioNet credentialing clears. It does **not** pretend synthetic data answer the research question. It only verifies that the same pipeline can ingest the expected ECHOVIEW schema, link videos to study/subject context, read DICOM metadata, create an uncertainty-aware review queue, and preserve human-review fields.

## What changed from the existing harness

- validates the exact 23 ECHOVIEW probability columns;
- records the top prediction, runner-up prediction, and top-2 probability margin;
- fixes `subject_id`/`study_id` recovery after pandas merge suffixing;
- preserves the existing metadata-leakage screen and 20-case decision gate;
- adds a deterministic synthetic fixture containing explicit view text, weaker workflow cues, and benign metadata.

## Run locally

```bash
python -m pip install pandas pydicom

python make_preaccess_fixture.py --out review/preaccess_fixture --n 36

python echoview_inspect_v2.py \
  --echoview review/preaccess_fixture/MIMIC_ECHO_View_Classifications.synthetic.csv \
  --record-list review/preaccess_fixture/echo-record-list.synthetic.csv \
  --dicom-root review/preaccess_fixture/dicom \
  --out review/preaccess_run \
  --n 20

python verify_preaccess_outputs.py --out review/preaccess_run
```

## Interpretation

A passing run means the **inspection contract** is ready. It says nothing about whether MIMIC metadata leaks view labels, whether ECHOVIEW is accurate, or whether study context explains model errors. Those remain the real-data decision gate once access is approved.

The next UI slice should consume `inspection_cases.csv` and make one review unit explicit:

`study → cine → top-2 ECHOVIEW predictions → confidence margin → surfaced metadata → accept / incorrect / uncertain / exclude → notes`

Do not add EF prediction, FHIR mapping, model training, or a generalized echo platform before the first real 20-case audit.
