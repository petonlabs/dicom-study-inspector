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


def acquisition_arguments(parser):
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-instances", type=int, default=MAX_INSTANCES)
    parser.add_argument("--max-bytes", type=int, default=MAX_DOWNLOAD_BYTES)
    parser.add_argument("--first-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--decoder", choices=["native", "gdcm"], default="native")


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    acquisition_arguments(sub.add_parser("acquire"))
    for command, output in [("compare", "out"), ("verify", "results")]:
        child = sub.add_parser(command)
        child.add_argument("--manifest", type=Path, required=True)
        child.add_argument("--" + output, type=Path, required=True)
    return parser


def execute(args):
    if args.command == "acquire":
        return acquire(
            args.out,
            args.max_instances,
            args.max_bytes,
            args.first_only,
            args.resume,
            args.decoder,
            validate_first=validate_first_comparison,
        )
    if args.command == "compare":
        return compare(args.manifest, args.out)
    return verify(args.manifest, args.results)


def main():
    parser = argument_parser()
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args), indent=2))
    except Exception as exc:
        parser.exit(1, f"{type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
