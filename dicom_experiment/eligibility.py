"""Header eligibility and decoder-upgrade policy, independent of acquisition I/O."""

from inspector import SUPPORTED

from .config import LOSSLESS_SYNTAXES

UID_FIELDS = {
    "study_uid": "StudyInstanceUID",
    "series_uid": "SeriesInstanceUID",
    "sop_uid": "SOPInstanceUID",
}


def header_fields(ds):
    return {
        "actual_modality": str(ds.get("Modality", "")),
        "transfer_syntax": str(ds.file_meta.TransferSyntaxUID),
        **{key: str(ds.get(tag, "")) for key, tag in UID_FIELDS.items()},
    }


def has_linkage(entry):
    return all(entry.get(key) for key in UID_FIELDS)


def require_eligible(entry, modality, decoder):
    syntax = entry["transfer_syntax"]
    if syntax not in SUPPORTED and not (decoder == "gdcm" and syntax in LOSSLESS_SYNTAXES):
        raise ValueError("unsupported_transfer_syntax:" + syntax)
    if entry["actual_modality"] != modality:
        raise ValueError("catalog_modality_mismatch")
    if not has_linkage(entry):
        raise ValueError("missing_required_uid")


def can_upgrade(entry, decoder):
    return (
        entry.get("status") == "excluded"
        and entry.get("reason", "").startswith("unsupported_transfer_syntax:")
        and decoder == "gdcm"
        and entry.get("transfer_syntax") in LOSSLESS_SYNTAXES
    )
