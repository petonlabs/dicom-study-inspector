# Scientific verification follow-up — 2026-10-22

**Recommendation: GO for a bounded technical methods note on the checked metadata,
pixel-conversion and sample-coverage results.** Independent technical review found
no remaining blockers in this scope. Human approval is still required before
publication; this work does not publish or merge anything.

## Baseline and completion semantics

Started from merged PR #2 on `main`, commit `c50a871`. The existing 72-test baseline
passed. This change fixes a reproduced false-success case: one successful comparison
and one failed intended comparison could previously yield `comparison_passed=true`.

The manifest's `accepted` objects define the intended experiment; rejected acquisition
candidates are counted separately. New summary fields report `intended_instances`,
`successful_instances`, `failed_instances`, `completion_status` and `instance_outcomes`.

| Status | Meaning |
|---|---|
| `empty` | No accepted input instances; no completion claim |
| `failed` | Intended inputs exist, but no successful comparison |
| `partial` | Useful successful comparisons coexist with failed/unaccounted inputs |
| `complete` | Every intended input has all three conditions, matching exported pixels and no failure |

`comparison_passed` now requires complete success. `first_milestone` may be true for
a partial run with a successful real-source comparison. `cross_modality_complete`
also requires CT/MR/PT/DX, two recorded dates, and at least one series with distinct,
nonempty SOP UIDs under consistent Study/Series linkage. Duplicate copies of one
SOP cannot establish a multi-instance series.

Loading/processing failures retain earlier successful results and explicit failure
records. Malformed manifests and duplicate manifest identifiers are fatal. A valid
partial result can pass `verify` with `completion_status=partial`; missing required
source evidence, inconsistent measurements or false completion claims cannot.
Existing CLI flags and artifact filenames are unchanged. Old summaries without the
new outcome fields are checked using evidence-derived status; legacy false-success
claims are rejected rather than grandfathered in.

## What is independently checked

The scientific audit does not call the producer's inventory, retention, pixel-metric,
measurement-assembly or summary functions. It reads source DICOM elements and sidecar
JSON through separate traversal code, reads actual PNG pixels, and reconstructs:

- Eligible and matched structured values for native DICOM, PNG-only and PNG plus
  sidecar/export manifest, including focused-field counts and per-instance fractions.
- Missing/empty/present inventory status, binary omission, sequence paths, exact-match
  flags and reported validation totals. Sequence containers, empty values and omitted
  binary payloads do not contribute to value-retention denominators.
- Dimensions, frames, source/export bit depth, distinct intensities, collapse fractions,
  forward-transform agreement and stored-pixel reconstruction errors.
- Accepted-instance coverage, reported failure accounting, modality/date coverage,
  hierarchy-conflict totals and the completion/acceptance gates.

Aggregate retention counts use independently reconstructed sets of `(instance, path)`
observations, not the producer's CSV or summary totals. CSV rows and summary values
are reconciled against that evidence; the report's embedded JSON must agree too.
Missing, duplicate and unexpected records fail verification. Integers and Booleans
must match exactly; finite floating metrics use `rtol=1e-12`, `atol=1e-12`. Null
fractions and non-applicable reconstruction values retain their distinct meaning.

The independent path shares pydicom serialization/decoding, the approved GDCM decoder,
low-level file/hash utilities and the declared field inventory. It is an independent
calculation, not an independent DICOM parser/codec implementation or external ground truth.

## Test and real-data evidence

- **153 tests pass**, including independent review's rerun. The 15-physical-line limit
  and existing dependency rules remain enforced; no new architecture framework was added.
- Adversarial tests alter counts, fractions, pixel metrics, coverage, outcomes, inventory
  and report values, then recompute ordinary checksums. Coordinated CSV/summary/report
  edits fail. Deliberately erroneous production calculations also fail the audit.
- Empty, all-failed, partial, complete, duplicate-identifier, missing-source and legacy
  cases are covered, including partial runs that still contain all four modalities.
- The unchanged ECHOVIEW workflow passes: **36 linked synthetic fixtures, 20 inspected
  cases**, zero subject/study linkage mismatches.
- All **19 frozen source hashes** matched; **14 accepted instances** received independent
  scientific reconciliation. The five excluded objects received source-integrity checks,
  not successful scientific-comparison claims. No new downloads were performed.
- The prior frozen outputs pass the new scientific audit. Two fresh comparison/verification
  runs each complete **14/14 intended comparisons with zero failures**, and all **76
  artifact hashes** match between runs. Both real-data acceptance gates pass.

| Representation | All eligible values matched | Focused eligible values matched |
|---|---:|---:|
| Native DICOM | 1,463 / 1,463 | 531 / 531 |
| PNG alone | 28 / 1,463 | 28 / 531 |
| PNG + sidecar + export manifest | 1,379 / 1,463 | 531 / 531 |

**No measured discrepancy was found:** the previously reported 28/1,463 and
1,379/1,463 counts, focused totals and pixel metrics remain unchanged. All 74 artifacts
other than `summary.json` and `report.md` are byte-identical to the prior frozen run.
Those two artifacts changed only for the new completion fields and implementation
provenance. The false-success and duplicate-SOP coverage bugs were exposed by synthetic
counterexamples, not by a failure in this 14-instance real sample. Reading the real
inventory also required handling CSV fields larger than Python's default reader limit;
the audit now sizes that limit to the existing file and restores it afterward.

### Local reproduction

From the follow-up worktree, using the existing pinned Python environment:

```bash
python -m pytest -q
python make_preaccess_fixture.py --out review/echo-fixture --n 36
python echoview_inspect_v2.py \
  --echoview review/echo-fixture/MIMIC_ECHO_View_Classifications.synthetic.csv \
  --record-list review/echo-fixture/echo-record-list.synthetic.csv \
  --dicom-root review/echo-fixture/dicom --out review/echo --n 20
python verify_preaccess_outputs.py --out review/echo
python study_compare.py compare \
  --manifest ../osteosarc-comparison/review/osteosarc/manifest.json \
  --out review/final-scientific-results
python study_compare.py verify \
  --manifest ../osteosarc-comparison/review/osteosarc/manifest.json \
  --results review/final-scientific-results
```

The second run used `review/final-scientific-repeat`. Output directories must be fresh for
another comparison. The original local results remain unchanged under the prior
worktree's `review/osteosarc/solid-final-results`. Missing local evidence is an error;
these commands do not fetch replacements. Source/data artifacts and verification logs
remain ignored and local. CI uses only synthetic inputs and uploads no data artifacts.

## Publication boundary and unresolved limitations

GO covers only the documented exporter and checked observations in this one-individual,
14-instance, single-frame sample. It does not establish dataset-wide performance,
clinical information loss, diagnostic impact, cine behavior or generalizability.
The 8-bit intensity loss is a property of this display conversion, not an unavoidable
property of PNG, which can support other pixel representations.

This follow-up independently checks hierarchy-conflict totals and coverage, **not the
full spatial/temporal ordering content of `hierarchy.json`**. Do not describe those
ordering algorithms as independently validated by this audit. Historical transient
failure causes and free-text error explanations cannot be authenticated or necessarily
reproduced; the verifier checks their accounting against available evidence. Hashes
and consistency checks do not authenticate an attacker-controlled source manifest.

Keep any public note limited to aggregate methodology/results. No source objects,
images, metadata dumps, source manifests or dataset portions are committed or uploaded.
There are no unresolved technical blockers for that restricted note; broader scientific
or clinical claims would require additional validation and are outside this GO.
