"""Acquisition orchestration; analysis enters only through a validation callback."""

from collections.abc import Callable
from pathlib import Path

from .acquisition_state import (
    AcquisitionState,
    BudgetExhausted,
    close_interrupted_reservations,
    create_ledger,
    resume_ledger,
    validate_options,
)
from .artifacts import dump
from .catalog import Catalog
from .config import MAX_DOWNLOAD_BYTES, MAX_INSTANCES
from .downloads import Downloader
from .models import AcquisitionOptions, SampleRequest
from .selection import bootstrap_candidate, sample_requests, sample_series, study_groups
from .transport import ArchiveTransport, HttpArchiveTransport

__all__ = ["Source", "acquire", "close_interrupted_reservations"]


class Source:
    """Compatibility facade composing catalog access and reservation-backed downloads."""

    def __init__(self, out: Path, ledger: dict, transport: ArchiveTransport | None = None):
        self.state = AcquisitionState(out, ledger)
        transport = transport if transport is not None else HttpArchiveTransport()
        self.catalog, self.downloader = (
            Catalog(self.state, transport),
            Downloader(self.state, transport),
        )

    @property
    def out(self):
        return self.state.out

    @property
    def ledger(self):
        return self.state.ledger

    def save(self):
        self.state.save()

    def listing(self, prefix, delimiter=None, limit=200):
        return self.catalog.listing(prefix, delimiter, limit)

    def download(self, obj, role, modality):
        return self.downloader.download(obj, role, modality)

    def series_sample(self, study, role, modality, count, excluded_series=None):
        request = SampleRequest(study, role, modality, count, excluded_series)
        return sample_series(self, request)


def bootstrap_output(source):
    name = "first-comparison-" + str(len(source.ledger["objects"]))
    path, attempt = source.out / name, 1
    while path.exists():
        path = source.out / (name + "-" + str(attempt))
        attempt += 1
    return path


def verify_bootstrap(source, study, validate_first):
    if source.ledger.get("first_verified"):
        return
    bootstrap_candidate(source, study)
    manifest = source.out / "first-manifest.json"
    dump(manifest, source.ledger)
    output = bootstrap_output(source)
    if not validate_first(manifest, output):
        raise ValueError("First real comparison failed; do not expand sample")
    source.ledger.update(first_verified=True, first_results=str(output.relative_to(source.out)))
    source.save()


def expand_sample(source, groups):
    requests = sample_requests(groups, source.ledger["first_series"], source.state)
    try:
        for request in requests:
            sample_series(source, request)
    except BudgetExhausted as exc:
        source.state.note(str(exc))
    source.ledger["complete"] = True


def run_acquisition(options, transport, validate_first):
    validate_options(options)
    ledger = resume_ledger(options) if options.resume else create_ledger(options, transport)
    source = Source(options.out, ledger, transport)
    source.save()
    groups = study_groups(source)
    verify_bootstrap(source, groups["chest"][0], validate_first)
    if not options.first_only:
        expand_sample(source, groups)
    source.state.freeze_manifest()
    return source.state.summary()


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
    options = AcquisitionOptions(Path(out), max_instances, max_bytes, first_only, resume, decoder)
    transport = transport if transport is not None else HttpArchiveTransport()
    return run_acquisition(options, transport, validate_first)
