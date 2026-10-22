"""Download execution with persisted reservations and header-only eligibility checks."""

import pydicom

from .acquisition_state import finalize_exclusion
from .artifacts import inside, sha
from .catalog import object_url
from .eligibility import can_upgrade, has_linkage, header_fields, require_eligible


def validate_object(obj):
    if not isinstance(obj.get("key"), str) or not obj["key"]:
        raise ValueError("Invalid source object key")
    size = obj.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError("Object size changed or invalid; refresh selection explicitly")
    if not isinstance(obj.get("etag"), str):
        raise ValueError("Invalid source object etag")


class Downloader:
    def __init__(self, state, transport):
        self.state, self.transport = state, transport

    def download(self, obj, role, modality):
        validate_object(obj)
        previous = self.state.previous(obj["key"])
        if previous:
            return self.reuse(previous, modality)
        self.state.require_budget(obj["size"])
        if self.transport.content_length(object_url(obj["key"])) != obj["size"]:
            raise ValueError("Object size changed or invalid; refresh selection explicitly")
        entry = self.state.reserve(obj, role, modality)
        self.transfer(entry, modality)
        return entry

    def reuse(self, entry, modality):
        if can_upgrade(entry, self.state.ledger.get("decoder")):
            self.upgrade(entry, modality)
        if entry.get("actual_modality") and entry["actual_modality"] != modality:
            return {**entry, "status": "excluded", "reason": "catalog_modality_mismatch"}
        return entry

    def upgrade(self, entry, modality):
        if sha(inside(self.state.out, entry["path"])) != entry["sha256"]:
            raise ValueError("Previously downloaded source hash changed")
        if entry.get("actual_modality") == modality and has_linkage(entry):
            entry["previous_exclusion"] = entry.pop("reason")
            entry["status"] = "accepted"
            self.state.save()

    def transfer(self, entry, modality):
        path = inside(self.state.out, entry["path"])
        try:
            received = self.transport.download(entry["url"], path, entry["size"])
            entry.update(sha256=sha(path), downloaded_bytes=received)
            entry.update(header_fields(pydicom.dcmread(path, stop_before_pixels=True)))
            require_eligible(entry, modality, self.state.ledger.get("decoder"))
            entry["status"] = "accepted"
        except Exception as exc:
            finalize_exclusion(self.state.out, entry, str(exc))
        self.state.save()
