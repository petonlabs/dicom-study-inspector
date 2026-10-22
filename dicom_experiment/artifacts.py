"""Artifact persistence and reproducibility primitives, independent of DICOM services."""

import csv
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path

from .config import CHECKSUMS_FILE


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def dump(path, value):
    Path(path).write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )


def load(path):
    return json.loads(Path(path).read_text())


def inside(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Path escapes artifact root")
    return path


def versions():
    result = {
        "python": sys.version.split()[0],
        **{
            p: importlib.metadata.version(p)
            for p in ("pydicom", "numpy", "Pillow", "pandas", "pytest")
        },
    }
    try:
        result["python-gdcm"] = importlib.metadata.version("python-gdcm")
    except importlib.metadata.PackageNotFoundError:
        pass
    return result


def write_csv(path, rows):
    fields = sorted({k for r in rows for k in r}) or ["instance"]
    with Path(path).open("w", newline="") as f:
        writer = csv.DictWriter(f, fields)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    k: json.dumps(v, sort_keys=True)
                    if isinstance(v, (dict, list))
                    else v
                    for k, v in r.items()
                }
            )


def implementation_hashes() -> dict[str, str]:
    """Bind the CLI, package and unchanged exporter to each reproducible run."""
    root = Path(__file__).resolve().parent.parent
    files = [root / "study_compare.py", root / "inspector.py"]
    files.extend(sorted((root / "dicom_experiment").glob("*.py")))
    return {str(path.relative_to(root)): sha(path) for path in files}


def artifact_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != CHECKSUMS_FILE
    }
