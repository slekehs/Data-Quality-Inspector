"""An in-memory example for the guided tour's upload, checks, and report screens."""

from datetime import datetime, timezone
import hashlib
from io import StringIO

from .audit import add_audit
from .core import Config, inspect
from .readers import _read_delimited


_FILENAME = "tour-support-responses.csv"
_CSV = """evaluation_id,prompt,response,rating,explanation,category
tour-01,How do I update my email address?,Open Settings and choose Email.,4,Clear first step.,Account
tour-02,How do I update my email address?,Open Settings and choose Email.,2,Missing the confirmation step.,Account
tour-03,Where can I download my invoice?,Open Billing and select Download invoice.,7,Steps are easy to follow.,Billing
tour-04,How do I find my tracking link?,,3,No answer text was recorded.,Orders
tour-05,Where can I see past orders?,Open Order history.,5,Direct answer.,Orders
"""


def tour_example(app_version):
    dataset = _read_delimited(StringIO(_CSV), "csv")
    config = Config.from_dict({
        "required": ["response", "evaluation_id"], "unique_id": True,
        "ratings": {"rating": {"required": True, "numeric": True, "integer": False, "range": [1, 5]}},
    })
    inspection = inspect(dataset, config)
    report = add_audit(dataset, inspection, {
        "rule_origin": "standard", "prompt_field": "prompt", "response_field": "response",
        "duplicates": True, "conflicts": True,
    }).to_dict()
    report["audit"]["source"] = {
        "filename": _FILENAME, "app_version": app_version,
        "sha256": hashlib.sha256(_CSV.encode("utf-8")).hexdigest(),
        "inspected_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    return {
        "upload": {
            "filename": _FILENAME, "upload_id": "tour-example", "input_format": dataset.input_format,
            "columns": list(dataset.headers), "record_count": len(dataset.records), "preview_truncated": False,
            "preview": [{"record": record.number, "values": record.values} for record in dataset.records],
        },
        "result": {
            "filename": _FILENAME, "report_id": "tour-example", "report": report,
            "urls": dict.fromkeys(("html", "text", "json", "findings", "skipped", "zip", "review"), "#"),
        },
    }
