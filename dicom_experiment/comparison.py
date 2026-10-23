"""Offline comparison orchestration using injected decoder and exporter ports."""

from pathlib import Path

import pydicom

from .artifacts import inside, load, sha
from .completion import intended_ids
from .config import CONDITIONS, METADATA_FILE
from .decoding import Decoder, select_decoder
from .exporting import Exporter, InspectorExporter, export_instance, export_provenance
from .measurements import matched_inventory, measurements, recovered_conditions
from .metadata import inventory
from .models import ComparisonRun, InstanceComparison, InstanceInput
from .reporting import summary, write_results
from .structure import hierarchy


def load_instance(entry, root):
    source = inside(root, entry["path"])
    if not source.is_file() or sha(source) != entry["sha256"]:
        raise ValueError(f"Source missing or hash mismatch: {entry['id']}")
    dataset = pydicom.dcmread(source)
    return InstanceInput(entry, source, dataset, inventory(dataset))


def compare_instance(instance, out, decoder_name, decoder, exporter):
    exported = export_instance(instance, out, decoder, exporter)
    provenance = export_provenance(instance, exported, out, decoder_name)
    sidecar = load(exported.destination / METADATA_FILE)
    conditions = recovered_conditions(
        instance.inventory, exported.png, sidecar, provenance["original_transfer_syntax"]
    )
    records = measurements(instance, exported, conditions)
    rows = matched_inventory(instance.entry["id"], instance.inventory, conditions)
    return InstanceComparison(records, provenance, rows)


def require_success(result):
    labels = [row["condition"] for row in result.measurements]
    if len(labels) != len(CONDITIONS) or set(labels) != set(CONDITIONS):
        raise ValueError("Missing or duplicate comparison condition")
    if not all(row["png_matches_transform"] for row in result.measurements):
        raise ValueError("Exported pixels disagree with documented transformation")


def process_entry(run, entry, root, out, decoder_name, decoder, exporter):
    instance = None
    try:
        instance = load_instance(entry, root)
        run.items.append((entry["id"], instance.dataset))
        result = compare_instance(instance, out, decoder_name, decoder, exporter)
        require_success(result)
        run.accept(result)
    except Exception as exc:
        run.reject(entry["id"], instance, exc)


def compare_entries(manifest, root, out, decoder, exporter):
    intended = intended_ids(manifest)
    run = ComparisonRun()
    for entry in manifest["objects"]:
        if entry["id"] in intended:
            process_entry(
                run, entry, root, out, manifest.get("decoder", "native"), decoder, exporter
            )
    return run


def prepare_output(out):
    out = Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("Output must be empty; use a new directory for each run")
    out.mkdir(parents=True, exist_ok=True)
    return out


def compare(
    manifest_path: Path | str,
    out: Path | str,
    *,
    decoder_backend: Decoder | None = None,
    exporter: Exporter | None = None,
) -> dict:
    manifest_path = Path(manifest_path).resolve()
    manifest = load(manifest_path)
    intended_ids(manifest)
    out = prepare_output(out)
    decoder = select_decoder(manifest.get("decoder", "native"), decoder_backend)
    exporter = exporter if exporter is not None else InspectorExporter()
    return run_and_write(manifest_path, manifest, out, decoder, exporter)


def run_and_write(manifest_path, manifest, out, decoder, exporter):
    run = compare_entries(manifest, manifest_path.parent, out, decoder, exporter)
    tree = hierarchy(run.items)
    result = summary(manifest_path, manifest, run, tree)
    write_results(out, manifest_path, run, tree, result)
    return result
