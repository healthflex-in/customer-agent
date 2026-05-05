import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from docscanner.service import (  # noqa: E402
    summarize_report_from_path,
    summarize_report_from_url,
)


def write_summary(summary: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Summary written to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Summarize a medical report (PDF/Image) into JSON."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--input",
        type=Path,
        help="Path to the report file (PDF or image).",
    )
    group.add_argument(
        "--input-url",
        type=str,
        help="HTTPS/S3 URL to the report file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("report_summary.json"),
        help="Where to save the JSON summary.",
    )

    args = parser.parse_args()

    if args.input:
        if not args.input.exists():
            parser.error(f"Input file '{args.input}' does not exist.")
        summary = summarize_report_from_path(args.input)
    else:
        summary = summarize_report_from_url(args.input_url)  # type: ignore[arg-type]

    write_summary(summary, args.output)


if __name__ == "__main__":
    main()