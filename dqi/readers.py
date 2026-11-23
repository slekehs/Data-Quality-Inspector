"""Read supported text datasets without changing their source files."""

from __future__ import annotations

import csv
import gzip
import zlib
from pathlib import Path
from typing import TextIO

from .core import Dataset, DatasetError, Record
from .json_utils import loads_json


FORMATS = {"csv", "tsv", "json", "jsonl", "ndjson"}


def _input_format(path: Path, override: str | None) -> str:
    if override is not None:
        if not isinstance(override, str) or override.lower() not in FORMATS:
            raise DatasetError("Format must be csv, tsv, json, jsonl, or ndjson.")
        selected = override.lower()
    else:
        uncompressed_path = path.with_suffix("") if path.suffix.lower() == ".gz" else path
        selected = uncompressed_path.suffix.lower().lstrip(".")
        if selected not in FORMATS:
            raise DatasetError(
                "Cannot detect a supported format from the filename. Use .csv, .tsv, "
                ".json, .jsonl, or .ndjson (optionally followed by .gz), "
                "or specify --format."
            )
    return "jsonl" if selected == "ndjson" else selected


def _read_delimited(handle: TextIO, input_format: str) -> Dataset:
    reader = csv.reader(handle, delimiter="\t" if input_format == "tsv" else ",", strict=True)
    label = input_format.upper()
    records = []
    try:
        headers = next(reader, None)
        if not headers:
            raise DatasetError(f"The {label} is empty or has no header row.")
        if any(not header.strip() for header in headers):
            raise DatasetError(f"The {label} contains a blank column name.")
        if len(set(headers)) != len(headers):
            raise DatasetError(f"The {label} contains duplicate column names.")
        while True:
            source_line = reader.line_num + 1
            cells = next(reader, None)
            if cells is None:
                break
            if not cells:
                continue
            if len(cells) != len(headers):
                raise DatasetError(
                    f"{label} record starting on line {source_line} has "
                    f"{len(cells)} cells; expected {len(headers)}."
                )
            records.append(Record(len(records) + 1, source_line, dict(zip(headers, cells))))
    except csv.Error as exc:
        raise DatasetError(f"{label} parsing failed near physical line {reader.line_num}: {exc}.") from exc
    return Dataset(tuple(headers), tuple(records), input_format)


def _parse_json(text: str, location: str):
    try:
        return loads_json(text)
    except ValueError as exc:
        raise DatasetError(f"Invalid JSON {location}: {exc}.") from exc
    except RecursionError as exc:
        raise DatasetError(f"JSON {location} is nested too deeply to read.") from exc


def _record_object(value, location: str) -> dict:
    if not isinstance(value, dict):
        raise DatasetError(f"{location} must be a JSON object containing named fields.")
    if any(not key.strip() for key in value):
        raise DatasetError(f"{location} contains a blank field name.")
    return value


def _read_jsonl(handle: TextIO) -> Dataset:
    headers: dict[str, None] = {}
    records = []
    for source_line, text in enumerate(handle, start=1):
        if not text.strip():
            continue
        location = f"on physical line {source_line}"
        values = _record_object(_parse_json(text, location), f"JSONL record {location}")
        headers.update(dict.fromkeys(values))
        records.append(Record(len(records) + 1, source_line, values))
    return Dataset(tuple(headers), tuple(records), "jsonl")


def _read_json(handle: TextIO) -> Dataset:
    document = _parse_json(handle.read(), "in the input file")
    if not isinstance(document, list):
        raise DatasetError(
            "JSON input must be a top-level array of record objects. "
            "Use JSONL for one object per line."
        )
    headers: dict[str, None] = {}
    records = []
    for number, item in enumerate(document, start=1):
        values = _record_object(item, f"JSON array record {number}")
        headers.update(dict.fromkeys(values))
        # A record may span many JSON lines; its array position is authoritative.
        records.append(Record(number, None, values))
    return Dataset(tuple(headers), tuple(records), "json")


def load_dataset(path: str | Path, format: str | None = None) -> Dataset:
    """Read UTF-8 CSV, TSV, JSON records, or JSONL, optionally gzip-compressed.

    Format selection uses the extension unless overridden. JSON field values keep
    their native types and missing fields remain absent. Compression is selected
    by the final ``.gz`` suffix, independently of the selected record format.
    """
    path = Path(path)
    input_format = _input_format(path, format)
    compressed = path.suffix.lower() == ".gz"
    try:
        opener = gzip.open if compressed else open
        with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
            if input_format in {"csv", "tsv"}:
                return _read_delimited(handle, input_format)
            if input_format == "jsonl":
                return _read_jsonl(handle)
            return _read_json(handle)
    except UnicodeDecodeError as exc:
        raise DatasetError("The input must use UTF-8 encoding.") from exc
    except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
        raise DatasetError(f"Cannot read gzip data: the file is invalid or incomplete ({exc}).") from exc
