"""Osteosarc-specific bounded sample selection, separate from storage and transport."""

from .config import INSTANCE_LIST_LIMIT, SERIES_LIST_LIMIT, STUDY_LIST_LIMIT
from .models import SampleRequest


def study_groups(source):
    studies, _, truncated = source.listing("dicom/", "/", STUDY_LIST_LIMIT)
    if truncated:
        source.state.note("Study list truncated at 200; selection applies only to listed prefixes")
    groups = {
        "chest": [p for p in studies if "_CT_" in p and "CHEST" in p],
        "mr": [p for p in studies if "_MR_" in p],
        "pt": [p for p in studies if "PET" in p],
        "dx": [p for p in studies if "_DX_" in p],
    }
    if not groups["chest"]:
        raise ValueError("No CT chest study in bounded source listing")
    return groups


def series_candidates(source, request, report_truncation=True):
    prefixes, _, truncated = source.listing(request.study, "/", SERIES_LIST_LIMIT)
    if truncated and report_truncation:
        source.state.note("Series listing truncated at 100: " + request.role)
    for prefix in prefixes:
        if prefix == request.excluded_series:
            continue
        _, objects, _ = source.listing(prefix, limit=INSTANCE_LIST_LIMIT)
        objects = [obj for obj in objects if obj["key"].lower().endswith(".dcm")]
        if len(objects) >= request.count:
            yield prefix, objects


def sample_series(source, request):
    for prefix, objects in series_candidates(source, request):
        first = source.download(objects[0], request.role, request.modality)
        if first["status"] == "accepted":
            for obj in objects[1 : request.count]:
                source.download(obj, request.role, request.modality)
            return prefix
    source.state.note("No eligible series within bounded listing: " + request.role)
    return None


def sample_requests(groups, first_series, state):
    chest = groups["chest"]
    requests = [
        SampleRequest(chest[0], "first_ct", "CT", 3),
        SampleRequest(chest[0], "second_ct_series", "CT", 1, first_series),
    ]
    if len(chest) > 1:
        requests.append(SampleRequest(chest[1], "later_ct", "CT", 3))
    for role, modality, count in [("mr", "MR", 3), ("pt", "PT", 3), ("dx", "DX", 1)]:
        if groups[role]:
            requests.append(SampleRequest(groups[role][0], role, modality, count))
        else:
            state.note("Missing catalog modality: " + modality)
    return requests


def bootstrap_candidate(source, study):
    request = SampleRequest(study, "first_ct", "CT", 3)
    for prefix, objects in series_candidates(source, request, report_truncation=False):
        candidate = source.download(objects[0], "first_ct", "CT")
        if candidate["status"] == "accepted":
            source.ledger["first_series"] = prefix
            return candidate
    source.state.freeze_manifest()
    raise ValueError("No supported initial CT within bounded acquisition")
