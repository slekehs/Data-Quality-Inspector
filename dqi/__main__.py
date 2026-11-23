"""Run with: python3 -m dqi sample.csv --output-dir reports."""

import argparse
import csv
import sys
from pathlib import Path

from .core import Config, inspect, load_dataset
from .json_utils import dumps_json, loads_json
from .presets import HELPSTEER2
from .reporting import render_html, render_text


def csv_value(value):
    text = value if isinstance(value, str) else dumps_json(value, indent=None)
    text = text.encode("utf-8", errors="backslashreplace").decode("utf-8")
    # JSON keeps exact values; CSV uses a literal-text prefix for spreadsheet formulas.
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def write_review_queue(handle, rows):
    writer = csv.writer(handle)
    fields = ("record", "source_line", "priority", "reasons", "prompt", "response", "ratings")
    writer.writerow(fields)
    for row in rows:
        writer.writerow([csv_value(row.get(key) if row.get(key) is not None else "") for key in fields])


def export(result, destination, source, overwrite=False, config_source=None):
    destination = Path(destination)
    report = result.to_dict()
    names = ["report.json", "findings.csv", "skipped-checks.csv", "summary.html", "summary.txt"]
    if "audit" in report:
        names.append("review-queue.csv")
    paths = [destination / name for name in names]
    protected = [Path(source)] + ([Path(config_source)] if config_source else [])
    for path in paths:
        for original in protected:
            if path.resolve() == original.resolve() or (path.exists() and original.exists() and path.samefile(original)):
                raise ValueError("Report output would overwrite an input file. Choose another output directory.")
    existing = [path.name for path in paths if path.exists()]
    if existing and not overwrite:
        raise ValueError("Reports already exist: " + ", ".join(existing) + ". Choose another directory or use --overwrite.")
    json_content = dumps_json(report) + "\n"
    html_content = render_html(report, source_name=Path(source).name)
    text_content = render_text(report, source_name=Path(source).name)
    destination.mkdir(parents=True, exist_ok=True)
    paths[0].write_text(json_content, encoding="utf-8")
    for path, rows in zip(paths[1:3], (result.findings, result.skipped_checks)):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["record", "source_line", "field", "value", "value_type", "field_present", "rule", "reason", "related_records"])
            for row in rows:
                writer.writerow([csv_value(value) for value in (row.record, row.source_line if row.source_line is not None else "", row.field, row.value if row.field_present else "", row.value_type, row.field_present, row.rule, row.reason, ";".join(map(str, row.related_records)))])
    paths[3].write_text(html_content, encoding="utf-8")
    paths[4].write_text(text_content, encoding="utf-8")
    if "audit" in report:
        with (destination / "review-queue.csv").open("w", encoding="utf-8", newline="") as handle:
            write_review_queue(handle, report["audit"].get("review_queue", []))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect CSV, TSV, JSON, or JSONL datasets, including gzip-compressed files.")
    parser.add_argument("input_file", type=Path)
    parser.add_argument("--format", choices=("csv", "tsv", "json", "jsonl", "ndjson"), help="Override format detection from the filename")
    settings = parser.add_mutually_exclusive_group()
    settings.add_argument("--config", type=Path, help="JSON rule configuration; omitted fields use the preset defaults")
    settings.add_argument("--preset", choices=("helpsteer2",), help="Use the built-in five-attribute HelpSteer2 rules")
    parser.add_argument("--output-dir", type=Path, help="Save readable HTML/text summaries and JSON/CSV reports; otherwise print JSON")
    parser.add_argument("--overwrite", action="store_true", help="Replace generated reports, never the source file")
    args = parser.parse_args(argv)
    try:
        raw = HELPSTEER2 if args.preset == "helpsteer2" else loads_json(args.config.read_text(encoding="utf-8-sig")) if args.config else {}
        config = Config.from_dict(raw)
        result = inspect(load_dataset(args.input_file, format=args.format), config)
        if args.output_dir:
            export(result, args.output_dir, args.input_file, args.overwrite, args.config)
            print(f"{result.status}: {result.total_records} records, {result.affected_records} affected, {len(result.findings)} findings, {len(result.skipped_checks)} skipped checks.")
            print(f"Reports: {args.output_dir.resolve()}")
            print(f"Readable summary: {(args.output_dir / 'summary.html').resolve()}")
        else:
            print(dumps_json(result.to_dict()))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return {"passed": 0, "issues_found": 1}.get(result.status, 3)


if __name__ == "__main__":
    raise SystemExit(main())
