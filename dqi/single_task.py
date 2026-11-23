"""Prepare a first-pass rubric evaluation directly from a trainer's pasted task."""

from .core import ConfigError
from .semantic import (MAX_RECORD_CHARS, SINGLE_TASK_INSTRUCTIONS, freeze_review_rules,
                       validate_review_rubrics)


TASK_FIELDS = {"prompt", "response", "reference", "rubrics", "rubric_filename"}


def prepare_single_task(payload):
    context = {key: payload.get(key) for key in ("prompt", "response", "reference")}
    for key, label in (("prompt", "prompt"), ("response", "AI response")):
        if not isinstance(context[key], str) or not context[key].strip():
            raise ConfigError(f"Paste the {label} before previewing the evaluation.")
    reference = context["reference"]
    if reference is not None and not isinstance(reference, str):
        raise ConfigError("Provide the optional reference as text.")
    if sum(len(value or "") for value in context.values()) > MAX_RECORD_CHARS:
        raise ConfigError("The prompt, response, and reference together must fit within 40,000 characters.")
    attributes = validate_review_rubrics(payload.get("rubrics"))
    filename = payload.get("rubric_filename")
    if filename is not None and (not isinstance(filename, str) or not filename.strip()
                                 or len(filename.encode("utf-8")) > 255 or filename in {".", ".."}
                                 or any(ch in "/\\" or ord(ch) < 32 or ord(ch) == 127 for ch in filename)):
        raise ConfigError("Provide the rubric filename without a folder path.")
    text_values = [value for value in context.values() if value is not None]
    text_values.extend(item[key] for item in attributes for key in ("field", "description"))
    for value in text_values:
        if "\x00" in value or any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise ConfigError("The task or rubric contains invalid text. Paste valid Unicode text.")
    context.update(record=1, source_line=None, original_ratings={}, review_mode="single_task")
    return {
        "review_mode": "single_task", "records": [context], "selected_count": 1, "total_records": 1,
        "options": {"rubrics": attributes}, "attributes": attributes,
        "selection_note": "One pasted response evaluated against the supplied rubric.",
        "rubric_source": filename or "User-supplied rubric",
        **freeze_review_rules(attributes, SINGLE_TASK_INSTRUCTIONS),
    }
