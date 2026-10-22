"""Anonymous, bounded GET-only acquisition from the documented public S3 archive."""

import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pydicom

from inspector import SUPPORTED

from .artifacts import dump, inside, load, sha, versions
from .config import (
    CATALOG_URL,
    DATASET_NAME,
    GDCM_VERSION,
    INSTANCE_LIST_LIMIT,
    LICENSE_URL,
    LOSSLESS_SYNTAXES,
    MAX_DOWNLOAD_BYTES,
    MAX_INSTANCES,
    S3_ROOT,
    SERIES_LIST_LIMIT,
    STUDY_LIST_LIMIT,
)
from .transport import ArchiveTransport, HttpArchiveTransport

NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}


class Source:
    def __init__(
        self, out: Path, ledger: dict, transport: ArchiveTransport | None = None
    ):
        self.out, self.ledger = Path(out), ledger
        self.transport = transport if transport is not None else HttpArchiveTransport()

    def save(self):
        dump(self.out / "acquisition-ledger.json", self.ledger)

    def listing(self, prefix, delimiter=None, limit=200):
        params = {"list-type": "2", "prefix": prefix, "max-keys": limit}
        if delimiter:
            params["delimiter"] = delimiter
        url = S3_ROOT + "?" + urllib.parse.urlencode(params)
        cache = (
            self.out / "catalog" / (str(len(self.ledger["listings"])).zfill(3) + ".xml")
        )
        existing = next((r for r in self.ledger["listings"] if r["url"] == url), None)
        if existing:
            cache = self.out / existing["path"]
            if sha(cache) != existing["sha256"]:
                raise ValueError("Cached listing changed")
            data = cache.read_bytes()
        else:
            data = self.transport.metadata(url)
            cache.write_bytes(data)
            self.ledger["listings"].append(
                {
                    "url": url,
                    "path": str(cache.relative_to(self.out)),
                    "sha256": sha(cache),
                }
            )
            self.save()
        root = ET.fromstring(data)
        prefixes = [p.text for p in root.findall("s:CommonPrefixes/s:Prefix", NS)]
        objects = [
            {
                "key": e.find("s:Key", NS).text,
                "size": int(e.find("s:Size", NS).text),
                "etag": e.find("s:ETag", NS).text,
            }
            for e in root.findall("s:Contents", NS)
        ]
        return prefixes, objects, root.find("s:IsTruncated", NS).text == "true"

    def download(self, obj, role, modality):
        previous = next(
            (e for e in self.ledger["objects"] if e["key"] == obj["key"]), None
        )
        if previous:
            if (
                previous.get("status") == "excluded"
                and previous.get("reason", "").startswith(
                    "unsupported_transfer_syntax:"
                )
                and self.ledger.get("decoder") == "gdcm"
                and previous.get("transfer_syntax") in LOSSLESS_SYNTAXES
            ):
                if sha(self.out / previous["path"]) != previous["sha256"]:
                    raise ValueError("Previously downloaded source hash changed")
                if previous.get("actual_modality") == modality and all(
                    previous.get(k) for k in ("study_uid", "series_uid", "sop_uid")
                ):
                    previous["previous_exclusion"] = previous.pop("reason")
                    previous["status"] = "accepted"
                    self.save()
            if (
                previous.get("actual_modality")
                and previous["actual_modality"] != modality
            ):
                return {
                    **previous,
                    "status": "excluded",
                    "reason": "catalog_modality_mismatch",
                }
            return previous
        size = obj["size"]
        if (
            len(self.ledger["objects"]) >= self.ledger["max_instances"]
            or self.ledger["charged_bytes"] + size > self.ledger["max_bytes"]
        ):
            raise ValueError("Acquisition budget exhausted")
        url = S3_ROOT + urllib.parse.quote(obj["key"], safe="/")
        advertised = self.transport.content_length(url)
        if advertised != size or size <= 0:
            raise ValueError(
                "Object size changed or invalid; refresh selection explicitly"
            )
        name = f"instance-{len(self.ledger['objects']):03d}"
        entry = {
            "id": name,
            "key": obj["key"],
            "url": url,
            "size": size,
            "path": f"sources/{name}.dcm",
            "role": role,
            "expected_modality": modality,
            "status": "reserved",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "etag": obj["etag"],
        }
        # Reserve the full object BEFORE GET. Interrupted/failed attempts consume their
        # reservation and are never silently retried, so resume cannot evade either cap.
        self.ledger["objects"].append(entry)
        self.ledger["charged_bytes"] += size
        self.save()
        path = self.out / entry["path"]
        received = 0
        try:
            received = self.transport.download(url, path, size)
            entry.update(sha256=sha(path), downloaded_bytes=received)
            ds = pydicom.dcmread(path, stop_before_pixels=True)
            syntax = str(ds.file_meta.TransferSyntaxUID)
            entry.update(
                actual_modality=str(ds.get("Modality", "")),
                transfer_syntax=syntax,
                study_uid=str(ds.get("StudyInstanceUID", "")),
                series_uid=str(ds.get("SeriesInstanceUID", "")),
                sop_uid=str(ds.get("SOPInstanceUID", "")),
            )
            if syntax not in SUPPORTED and not (
                self.ledger.get("decoder") == "gdcm" and syntax in LOSSLESS_SYNTAXES
            ):
                raise ValueError("unsupported_transfer_syntax:" + syntax)
            if entry["actual_modality"] != modality:
                raise ValueError("catalog_modality_mismatch")
            if not all(entry[k] for k in ("study_uid", "series_uid", "sop_uid")):
                raise ValueError("missing_required_uid")
            # Full decode is bounded by inspector in compare; this is metadata eligibility.
            entry["status"] = "accepted"
        except Exception as exc:
            received = path.stat().st_size if path.exists() else received
            entry.update(status="excluded", reason=str(exc), downloaded_bytes=received)
            if path.exists():
                entry["sha256"] = sha(path)
        self.save()
        return entry

    def series_sample(self, study, role, modality, count, excluded_series=None):
        prefixes, _, truncated = self.listing(study, "/", SERIES_LIST_LIMIT)
        if truncated:
            self.ledger["notes"].append("Series listing truncated at 100: " + role)
        for prefix in prefixes:
            if prefix == excluded_series:
                continue
            _, objects, _ = self.listing(prefix, limit=INSTANCE_LIST_LIMIT)
            objects = [o for o in objects if o["key"].lower().endswith(".dcm")]
            if len(objects) < count:
                continue
            first = self.download(objects[0], role, modality)
            if first["status"] != "accepted":
                continue
            # Keep consecutive object order; do not replace individual failed slices.
            for obj in objects[1:count]:
                self.download(obj, role, modality)
            return prefix
        self.ledger["notes"].append(
            "No eligible series within bounded listing: " + role
        )
        self.save()
        return None


def close_interrupted_reservations(out, ledger):
    """Finalize interrupted attempts without retrying them or refunding their caps."""
    for entry in ledger["objects"]:
        if entry.get("status") != "reserved":
            continue
        path = inside(out, entry["path"])
        entry.update(
            status="excluded",
            reason="interrupted_download_reservation",
            downloaded_bytes=path.stat().st_size if path.exists() else 0,
        )
        if path.is_file():
            entry["sha256"] = sha(path)


def acquire(
    out,
    max_instances=MAX_INSTANCES,
    max_bytes=MAX_DOWNLOAD_BYTES,
    first_only=False,
    resume=False,
    decoder="native",
    *,
    validate_first: Callable[[Path, Path], bool],
    transport: ArchiveTransport | None = None,
) -> dict:
    out = Path(out)
    transport = transport if transport is not None else HttpArchiveTransport()
    if not (
        1 <= max_instances <= MAX_INSTANCES and 1 <= max_bytes <= MAX_DOWNLOAD_BYTES
    ):
        raise ValueError(
            "Limits must be positive and no greater than 20 objects / 100 MiB"
        )
    if decoder == "gdcm" and versions().get("python-gdcm") != GDCM_VERSION:
        raise ValueError(
            "Install requirements-real-data.txt for the pinned approved decoder"
        )
    ledger_path = out / "acquisition-ledger.json"
    if resume:
        ledger = load(ledger_path)
        if (max_instances, max_bytes) != (ledger["max_instances"], ledger["max_bytes"]):
            raise ValueError("Resume must keep original limits")
        if ledger.get("complete"):
            raise ValueError(
                "Acquisition frozen; use existing manifest for offline reruns"
            )
        close_interrupted_reservations(out, ledger)
        if ledger.get("decoder", "native") != decoder:
            if ledger.get("first_verified"):
                raise ValueError("Cannot change decoder after verified bootstrap")
            ledger["notes"].append(
                "Explicit decoder change: "
                + ledger.get("decoder", "native")
                + " -> "
                + decoder
            )
            ledger["decoder"] = decoder
            ledger["versions"] = versions()
    else:
        if out.exists() and any(out.iterdir()):
            raise ValueError("Acquisition directory must be empty (or use --resume)")
        (out / "catalog").mkdir(parents=True, exist_ok=True)
        (out / "sources").mkdir()
        license_data = transport.metadata(LICENSE_URL)
        if b"CC0" not in license_data:
            raise ValueError(
                "Expected documented CC0 conditions not found; stop for review"
            )
        (out / "catalog" / "access.html").write_bytes(license_data)
        (out / "catalog" / "studies.html").write_bytes(transport.metadata(CATALOG_URL))
        ledger = {
            "schema_version": 1,
            "dataset": DATASET_NAME,
            "license_url": LICENSE_URL,
            "catalog_url": CATALOG_URL,
            "license": "CC0 1.0",
            "catalog_sha256": sha(out / "catalog" / "studies.html"),
            "access_sha256": sha(out / "catalog" / "access.html"),
            "versions": versions(),
            "decoder": decoder,
            "max_instances": max_instances,
            "max_bytes": max_bytes,
            "charged_bytes": 0,
            "objects": [],
            "listings": [],
            "notes": [],
            "selection": "Earliest study folder by date; S3 catalog series/object lexicographic order; first series with required sample count. No anatomical selection. Actual headers determine modality.",
        }
    source = Source(out, ledger, transport)
    source.save()
    studies, _, truncated = source.listing("dicom/", "/", STUDY_LIST_LIMIT)
    if truncated:
        ledger["notes"].append(
            "Study list truncated at 200; selection applies only to listed prefixes"
        )
    chest = [p for p in studies if "_CT_" in p and "CHEST" in p]
    mr = [p for p in studies if "_MR_" in p]
    pt = [p for p in studies if "PET" in p]
    dx = [p for p in studies if "_DX_" in p]
    if not chest:
        raise ValueError("No CT chest study in bounded source listing")
    if not ledger.get("first_verified"):
        # Bootstrap one object from a series with at least three catalog entries.
        prefixes, _, _ = source.listing(chest[0], "/", SERIES_LIST_LIMIT)
        first = None
        for prefix in prefixes:
            _, objects, _ = source.listing(prefix, limit=INSTANCE_LIST_LIMIT)
            objects = [o for o in objects if o["key"].lower().endswith(".dcm")]
            if len(objects) < 3:
                continue
            candidate = source.download(objects[0], "first_ct", "CT")
            if candidate["status"] == "accepted":
                first = candidate
                ledger["first_series"] = prefix
                break
        if first is None:
            dump(out / "manifest.json", ledger)
            raise ValueError("No supported initial CT within bounded acquisition")
        dump(out / "first-manifest.json", ledger)
        first_out = out / ("first-comparison-" + str(len(ledger["objects"])))
        attempt = 1
        while first_out.exists():
            first_out = out / (
                "first-comparison-" + str(len(ledger["objects"])) + "-" + str(attempt)
            )
            attempt += 1
        if not validate_first(out / "first-manifest.json", first_out):
            raise ValueError("First real comparison failed; do not expand sample")
        ledger["first_verified"] = True
        ledger["first_results"] = str(first_out.relative_to(out))
        source.save()
    if not first_only:
        tasks = [
            (chest[0], "first_ct", "CT", 3, None),
            (chest[0], "second_ct_series", "CT", 1, ledger["first_series"]),
        ]
        if len(chest) > 1:
            tasks.append((chest[1], "later_ct", "CT", 3, None))
        for candidates, role, modality, n in [
            (mr, "mr", "MR", 3),
            (pt, "pt", "PT", 3),
            (dx, "dx", "DX", 1),
        ]:
            if candidates:
                tasks.append((candidates[0], role, modality, n, None))
            else:
                ledger["notes"].append("Missing catalog modality: " + modality)
        try:
            for task in tasks:
                source.series_sample(*task)
        except ValueError as exc:
            ledger["notes"].append(str(exc))
        ledger["complete"] = True
    source.save()
    dump(out / "manifest.json", ledger)
    return {
        "downloaded_objects": len(ledger["objects"]),
        "charged_bytes": ledger["charged_bytes"],
        "accepted_objects": sum(e["status"] == "accepted" for e in ledger["objects"]),
        "first_comparison_verified": ledger["first_verified"],
        "frozen": ledger.get("complete", False),
        "notes": ledger["notes"],
    }
