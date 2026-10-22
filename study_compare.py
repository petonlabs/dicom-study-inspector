"""CLI composition for bounded local DICOM comparison; analysis services are offline."""

import argparse
import json
from pathlib import Path

from dicom_experiment.acquisition import acquire
from dicom_experiment.comparison import compare
from dicom_experiment.config import MAX_DOWNLOAD_BYTES, MAX_INSTANCES
from dicom_experiment.verification import verify


def validate_first_comparison(manifest: Path, output: Path) -> bool:
    result = compare(manifest, output)
    if not result["first_milestone"]:
        return False
    verify(manifest, output)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    acq = sub.add_parser("acquire")
    acq.add_argument("--out", type=Path, required=True)
    acq.add_argument("--max-instances", type=int, default=MAX_INSTANCES)
    acq.add_argument("--max-bytes", type=int, default=MAX_DOWNLOAD_BYTES)
    acq.add_argument("--first-only", action="store_true")
    acq.add_argument("--resume", action="store_true")
    acq.add_argument("--decoder", choices=["native", "gdcm"], default="native")
    comp = sub.add_parser("compare")
    comp.add_argument("--manifest", type=Path, required=True)
    comp.add_argument("--out", type=Path, required=True)
    ver = sub.add_parser("verify")
    ver.add_argument("--manifest", type=Path, required=True)
    ver.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "acquire":
            result = acquire(
                args.out,
                args.max_instances,
                args.max_bytes,
                args.first_only,
                args.resume,
                args.decoder,
                validate_first=validate_first_comparison,
            )
        elif args.command == "compare":
            result = compare(args.manifest, args.out)
        else:
            result = verify(args.manifest, args.results)
        print(json.dumps(result, indent=2))
    except Exception as exc:
        parser.exit(1, f"{type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
