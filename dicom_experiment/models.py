"""Small internal records; serialized artifact schemas remain dictionaries."""

from dataclasses import dataclass, field
from pathlib import Path

from pydicom.dataset import Dataset

from .config import MAX_DOWNLOAD_BYTES, MAX_INSTANCES


@dataclass(frozen=True)
class AcquisitionOptions:
    out: Path
    max_instances: int = MAX_INSTANCES
    max_bytes: int = MAX_DOWNLOAD_BYTES
    first_only: bool = False
    resume: bool = False
    decoder: str = "native"


@dataclass(frozen=True)
class SampleRequest:
    study: str
    role: str
    modality: str
    count: int
    excluded_series: str | None = None


@dataclass(frozen=True)
class InstanceInput:
    entry: dict
    source: Path
    dataset: Dataset
    inventory: list[dict]


@dataclass(frozen=True)
class InstanceComparison:
    measurements: list[dict]
    export: dict
    inventory: list[dict]


@dataclass
class ComparisonRun:
    inventory: list[dict] = field(default_factory=list)
    measurements: list[dict] = field(default_factory=list)
    items: list[tuple[str, Dataset]] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    exports: list[dict] = field(default_factory=list)

    def accept(self, result: InstanceComparison):
        self.inventory.extend(result.inventory)
        self.measurements.extend(result.measurements)
        self.exports.append(result.export)

    def reject(self, name: str, instance: InstanceInput | None, error: Exception):
        if instance is not None:
            self.inventory.extend({"instance": name, **row} for row in instance.inventory)
        self.failures.append({"instance": name, "error": str(error)})
