# Next dispatch: reproduce view inference before training

Candidate: [ECHOVIEW v0.1](https://www.physionet.org/content/echoview/0.1/) linked to the underlying MIMIC-IV-ECHO DICOM clips. This requires separately authorized credentialed access; no data was retrieved here. ECHOVIEW supplies 23 machine-predicted view probabilities rather than general expert gold labels. Its narrow expert audit does not validate every class. Preserve the published label taxonomy, including occlusion and zoom variants.

Lowest-friction reproduction candidate is the pretrained VGG classifier and inference repository cited in ECHOVIEW: fixed seed 42, ten sampled frames per clip, mean class probabilities. First verify the pinned source, weights, licence and environment in a separate dispatch. No claim that it currently runs locally. EchoNet-Dynamic is A4C-only and therefore unsuitable as a standalone multiclass view benchmark ([dataset description](https://aimi.stanford.edu/datasets/echonet-dynamic-cardiac-ultrasound)).

Unit: one DICOM cine, never independent frames. Freeze a patient-level train/validation/test manifest using subject identity; keep all studies and clips for one patient together. Detect duplicate clips and linked/repeated acquisitions before splitting. Guard against burned-in view text, identifiers, vendor/site signatures, teacher pseudo-label leakage, and study-derived shortcuts. Exclude fields that explicitly encode the target label.

First evaluate frozen pixel-only inference on an independently expert-labeled, patient-held-out subset. Report macro-F1, balanced accuracy, per-view recall, confusion matrix and patient-bootstrap intervals only on legitimate expert references. Agreement with ECHOVIEW is teacher agreement, not accuracy. Keep ambiguous/unreadable clips explicit.

Then compare the same pixel encoder with a small acquisition-context branch using only predeclared available timing, dimensions, photometric and manufacturer metadata; measure missingness and vendor-stratified/generalization performance. Ablate each context family. Exclude measurements until provenance and semantic mapping are validated; never assume similarly named echo methods are interchangeable.

Compute planning estimate (not measured): a 100-clip, ten-frame inference smoke run should fit on CPU or one 8–16 GB GPU, likely minutes after decoding; benchmark throughput before committing a larger budget. No training or downloads were started. A later limited fine-tuning trial may need several GPU-hours, to be estimated from measured throughput and approved separately.
