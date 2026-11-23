"""Read a companion scoring rubric locally, preserving criteria and decimal scales."""

import csv
import io
from pathlib import Path
import re

from .core import _number
from .json_utils import loads_json
from .semantic import _finite


MAX_RUBRIC_BYTES = 256 * 1024
_MAX_FIELDS = 12
_REQUIRED = {"field", "description"}
_SCALE = {"min", "max", "integer"}
_INVALID_TEXT = re.compile(r"[\x00\ud800-\udfff]")


def _csv_entries(text):
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader, [])
        if len(set(header)) != len(header) or set(header) not in (_REQUIRED, _REQUIRED | _SCALE):
            raise ValueError("Rubric CSV headers must be field,description with optional min,max,integer columns.")
        entries = []
        for row in reader:
            if not row:
                continue
            if len(entries) == _MAX_FIELDS:
                raise ValueError(f"A rubric file can contain up to {_MAX_FIELDS} scoring fields.")
            if len(row) != len(header):
                raise ValueError(f"Rubric CSV line {reader.line_num} has {len(row)} cells. Expected {len(header)}.")
            entry = dict(zip(header, row))
            if _SCALE <= entry.keys():
                if all(not entry[key].strip() for key in _SCALE):
                    for key in _SCALE:
                        del entry[key]
                else:
                    whole = entry["integer"].strip().lower()
                    if whole not in {"true", "false"}:
                        raise ValueError(f"Rubric CSV line {reader.line_num} needs true or false in the integer column.")
                    entry["integer"] = whole == "true"
            entries.append(entry)
        return entries
    except csv.Error as exc:
        raise ValueError(f"Could not read rubric CSV near line {reader.line_num}. Check cell sizes and quoting.") from exc


def _entry(raw, index):
    label = f"Rubric entry {index}"
    if not isinstance(raw, dict) or set(raw) not in (_REQUIRED, _REQUIRED | _SCALE):
        raise ValueError(f"{label} needs field and description. To specify a scale, include min, max, and integer together.")
    field, description = raw["field"], raw["description"]
    if not isinstance(field, str) or not field.strip():
        raise ValueError(f"{label} needs a nonblank scoring field name.")
    if not isinstance(description, str) or not description.strip() or len(description) > 6000:
        raise ValueError(f"{label} needs a description between 1 and 6,000 characters.")
    if _INVALID_TEXT.search(field) or _INVALID_TEXT.search(description):
        raise ValueError(f"{label} contains invalid text. Save the rubric as UTF-8.")
    result = {"field": field, "description": description, "min": None, "max": None, "integer": None}
    if "integer" in raw:
        low = _finite(_number(raw["min"]), f"{label} minimum")
        high = _finite(_number(raw["max"]), f"{label} maximum")
        if low >= high:
            raise ValueError(f"{label} needs a minimum below its maximum.")
        if type(raw["integer"]) is not bool:
            raise ValueError(f"{label} needs true or false for integer.")
        if raw["integer"] and (low != low.to_integral_value() or high != high.to_integral_value()):
            raise ValueError(f"{label} needs whole-number endpoints for an integer scale.")
        # The browser must receive bounds as text to avoid rounding during JSON parsing.
        result.update(min=str(low), max=str(high), integer=raw["integer"])
    return result


def parse_rubric(filename, content):
    """Return validated entries in source order, ready for the user's mapping step."""
    if len(content) > MAX_RUBRIC_BYTES:
        raise ValueError("The rubric file exceeds the 256 KiB limit.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Save the rubric as a UTF-8 file and upload it again.") from exc
    source = Path(filename)
    suffix = source.suffix.lower()
    if suffix == ".json":
        entries = loads_json(text)
        if isinstance(entries, dict) and set(entries) == {"rubrics"}:
            entries = entries["rubrics"]
        if not isinstance(entries, list):
            raise ValueError('Use a JSON array of entries or an object with a single "rubrics" array.')
    elif suffix == ".csv":
        entries = _csv_entries(text)
    elif suffix in {".txt", ".md"}:
        entries = [{"field": source.stem, "description": text}]
    else:
        raise ValueError("Choose a rubric file in JSON, CSV, TXT, or Markdown (.md) format.")
    if not 1 <= len(entries) <= _MAX_FIELDS:
        raise ValueError(f"Provide between 1 and {_MAX_FIELDS} scoring fields in the rubric file.")
    result = []
    fields = set()
    for index, raw in enumerate(entries, 1):
        entry = _entry(raw, index)
        if entry["field"] in fields:
            raise ValueError(f"Rubric entry {index} repeats a scoring field name. Use each field name once.")
        fields.add(entry["field"])
        result.append(entry)
    return {"filename": filename, "entries": result}
