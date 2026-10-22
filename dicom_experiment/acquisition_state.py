"""Persisted acquisition lifecycle and monotonic object/byte reservations."""

from datetime import datetime, timezone
from pathlib import Path

from .artifacts import dump, inside, load, sha, versions
from .catalog import object_url
from .config import (
    CATALOG_URL,
    DATASET_NAME,
    GDCM_VERSION,
    LICENSE_URL,
    MAX_DOWNLOAD_BYTES,
    MAX_INSTANCES,
)

SOURCE_POLICY = {
    "schema_version": 1,
    "dataset": DATASET_NAME,
    "license_url": LICENSE_URL,
    "catalog_url": CATALOG_URL,
    "license": "CC0 1.0",
}

SELECTION = (
    "Earliest study folder by date; S3 catalog series/object lexicographic order; "
    "first series with required sample count. No anatomical selection. Actual headers determine modality."
)


class BudgetExhausted(ValueError):
    """Selection may finish partially when its explicit acquisition budget is spent."""


def validate_options(options):
    if not (
        1 <= options.max_instances <= MAX_INSTANCES and 1 <= options.max_bytes <= MAX_DOWNLOAD_BYTES
    ):
        raise ValueError("Limits must be positive and no greater than 20 objects / 100 MiB")
    if options.decoder == "gdcm" and versions().get("python-gdcm") != GDCM_VERSION:
        raise ValueError("Install requirements-real-data.txt for the pinned approved decoder")


def base_ledger(options):
    return {
        **SOURCE_POLICY,
        "versions": versions(),
        "decoder": options.decoder,
        "max_instances": options.max_instances,
        "max_bytes": options.max_bytes,
        "charged_bytes": 0,
        "objects": [],
        "listings": [],
        "notes": [],
        "selection": SELECTION,
    }


def create_ledger(options, transport):
    out = options.out
    if out.exists() and any(out.iterdir()):
        raise ValueError("Acquisition directory must be empty (or use --resume)")
    (out / "catalog").mkdir(parents=True, exist_ok=True)
    (out / "sources").mkdir()
    evidence = save_access_evidence(out, transport)
    return {**base_ledger(options), **evidence}


def save_access_evidence(out, transport):
    license_data = transport.metadata(LICENSE_URL)
    if b"CC0" not in license_data:
        raise ValueError("Expected documented CC0 conditions not found; stop for review")
    (out / "catalog/access.html").write_bytes(license_data)
    (out / "catalog/studies.html").write_bytes(transport.metadata(CATALOG_URL))
    return {
        "catalog_sha256": sha(out / "catalog/studies.html"),
        "access_sha256": sha(out / "catalog/access.html"),
    }


def resume_ledger(options):
    ledger = load(options.out / "acquisition-ledger.json")
    if (options.max_instances, options.max_bytes) != (ledger["max_instances"], ledger["max_bytes"]):
        raise ValueError("Resume must keep original limits")
    if ledger.get("complete"):
        raise ValueError("Acquisition frozen; use existing manifest for offline reruns")
    close_interrupted_reservations(options.out, ledger)
    if ledger.get("decoder", "native") != options.decoder:
        change_decoder(ledger, options.decoder)
    return ledger


def change_decoder(ledger, decoder):
    if ledger.get("first_verified"):
        raise ValueError("Cannot change decoder after verified bootstrap")
    previous = ledger.get("decoder", "native")
    ledger["notes"].append("Explicit decoder change: " + previous + " -> " + decoder)
    ledger.update(decoder=decoder, versions=versions())


def finalize_exclusion(out, entry, reason):
    path = inside(out, entry["path"])
    entry.update(
        status="excluded",
        reason=reason,
        downloaded_bytes=path.stat().st_size if path.exists() else 0,
    )
    if path.is_file():
        entry["sha256"] = sha(path)


def close_interrupted_reservations(out, ledger):
    """Interrupted attempts remain charged and are never silently retried."""
    for entry in ledger["objects"]:
        if entry.get("status") == "reserved":
            finalize_exclusion(out, entry, "interrupted_download_reservation")


def reservation(obj, role, modality, index):
    name = f"instance-{index:03d}"
    return {
        "id": name,
        "key": obj["key"],
        "url": object_url(obj["key"]),
        "size": obj["size"],
        "path": f"sources/{name}.dcm",
        "role": role,
        "expected_modality": modality,
        "status": "reserved",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "etag": obj["etag"],
    }


class AcquisitionState:
    def __init__(self, out, ledger):
        self.out, self.ledger = Path(out), ledger

    def save(self):
        dump(self.out / "acquisition-ledger.json", self.ledger)

    def require_budget(self, size):
        if (
            len(self.ledger["objects"]) >= self.ledger["max_instances"]
            or self.ledger["charged_bytes"] + size > self.ledger["max_bytes"]
        ):
            raise BudgetExhausted("Acquisition budget exhausted")

    def reserve(self, obj, role, modality):
        self.require_budget(obj["size"])
        entry = reservation(obj, role, modality, len(self.ledger["objects"]))
        self.ledger["objects"].append(entry)
        self.ledger["charged_bytes"] += obj["size"]
        self.save()  # Persist the full charge before any GET can start.
        return entry

    def previous(self, key):
        return next((e for e in self.ledger["objects"] if e["key"] == key), None)

    def note(self, message):
        self.ledger["notes"].append(message)
        self.save()

    def freeze_manifest(self):
        self.save()
        dump(self.out / "manifest.json", self.ledger)

    def summary(self):
        return {
            "downloaded_objects": len(self.ledger["objects"]),
            "charged_bytes": self.ledger["charged_bytes"],
            "accepted_objects": sum(e["status"] == "accepted" for e in self.ledger["objects"]),
            "first_comparison_verified": self.ledger["first_verified"],
            "frozen": self.ledger.get("complete", False),
            "notes": self.ledger["notes"],
        }
