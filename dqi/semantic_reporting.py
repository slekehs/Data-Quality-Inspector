"""Standalone, local exports for a bounded model-assisted answer review."""

import csv
from html import escape
from pathlib import Path
import zipfile

from .__main__ import csv_value
from .json_utils import dumps_json
from .report_appearance import REPORT_PALETTE


FILES = ("answer-review.html", "answer-review.json", "answer-review.csv")
COMPARISONS = {
    "supported": "Same suggested score",
    "close": "Within selected tolerance",
    "questionable": "Rating needs a closer look",
    "unscored": "Original rating cannot be compared",
    "insufficient_evidence": "Insufficient evidence",
}

REVIEW_STYLES = """
*{box-sizing:border-box}
body{margin:0;background:var(--canvas);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
main{max-width:1100px;margin:0 auto;padding:40px 24px}
h1{font-size:32px}
h2{font-size:24px}
h3{font-size:18px}
h4{font-size:16px}
article,.intro,details.rubrics{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:24px;margin:24px 0}
.muted{color:var(--muted)}
.badge{display:inline-block;padding:4px 12px;border-radius:18px;background:var(--positive-bg);color:var(--positive-ink);font-weight:600}
.notice{background:var(--attention-bg);color:var(--attention-ink);padding:16px;border-radius:8px}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.metrics div{border:1px solid var(--line);padding:14px;border-radius:9px}
.metrics span{display:block;font-size:14px}
.metrics strong{font-size:25px}
pre,blockquote{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;background:var(--inset);padding:16px;border-radius:8px}
blockquote{margin:8px 0 16px;border-left:3px solid var(--accent)}
.assessment{border-top:1px solid var(--line);padding:16px 0}
summary{cursor:pointer;font-weight:600}
a{color:var(--link)}
a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
code{overflow-wrap:anywhere}
.eyebrow{letter-spacing:.12em;color:var(--accent);font-size:14px;font-weight:700}
li{margin-bottom:8px}
@media print {
body{background:white}
details{display:block}
summary{display:none}
article{break-inside:auto}
}
"""


def _text(value):
    return "Not provided" if value is None else value if isinstance(value, str) else dumps_json(value, indent=None)


def _e(value):
    return escape(_text(value), quote=True)


def _paired_assessments(primary, row):
    first = {item["field"]: item for item in primary.get("assessments", [])}
    second = {item["field"]: item for item in row.get("assessments", [])}
    return [(field, first.get(field), second.get(field)) for field in dict.fromkeys((*first, *second))]


def _explanation(item):
    quotes = "".join(f"<li><strong>{_e(quote.get('source'))}</strong><blockquote>{_e(quote.get('quote'))}</blockquote></li>" for quote in item.get("evidence", []))
    limits = "".join(f"<li>{_e(limit)}</li>" for limit in item.get("limitations", []))
    return (
        f"<p>{_e(item.get('rationale'))}</p>"
        + (f"<h4>Evidence from the record</h4><ul>{quotes}</ul>" if quotes else "<p>No supporting excerpt was supplied.</p>")
        + (f"<h4>What still needs checking</h4><ul>{limits}</ul>" if limits else "")
    )


def _comparison_html(primary, report):
    row = next((row for row in report.get("rows", []) if row.get("record") == primary["record"]), {})
    single_task = report.get("review_mode") == "single_task"
    sections = []
    for field, first, second in _paired_assessments(primary, row):
        sections.append(f"<section class='assessment'><h3>{_e(field)}</h3>")
        if not single_task:
            sections.append(f"<p><b>Original rating</b> &nbsp; {_e((first or second).get('original_score'))}</p>")
        for label, model, item in (("Primary review", primary.get("model"), first),
                                   ("Second opinion", report.get("model"), second)):
            heading = f"{label} · {_e(model)}"
            if item is None:
                sections.append(f"<h4>{heading}</h4><p>No assessment returned.</p>")
                continue
            score = "Undecided" if item.get("suggested_score") is None else _e(item["suggested_score"])
            sections.append(f"<h4>{heading} · {score}</h4>" + _explanation(item))
        sections.append("</section>")
    heading = "Compare task evaluations" if single_task else f"Compare reviews for record {_e(primary['record'])}"
    return f"<article><h2>{heading}</h2>{''.join(sections)}</article>"


def render_html(report):
    """Escape all model and source content; retain full context in every export."""
    source = report.get("source", {})
    rows = report.get("rows", [])
    primary = report.get("primary_review")
    single_task = report.get("review_mode") == "single_task"
    criteria = {attr["field"]: attr for attr in report.get("attributes", [])}
    cards = []
    for row in rows:
        assessments = []
        regular_items = [] if primary and row["record"] == primary["record"] else row.get("assessments", [])
        for item in regular_items:
            if single_task:
                rule = criteria.get(item["field"], {})
                score = _e(item["suggested_score"]) if item.get("suggested_score") is not None else "Insufficient evidence"
                scores = f"<p class='badge'>{score}</p><p>Rubric scale &nbsp; {_e(rule.get('min'))}–{_e(rule.get('max'))}</p>"
            else:
                scores = (f"<p class='badge'>{_e(COMPARISONS.get(item.get('comparison'), item.get('comparison')))}</p>"
                          f"<p><strong>Existing score:</strong> {_e(item.get('original_score'))} · <strong>Model-suggested score:</strong> {_e(item.get('suggested_score'))} · <strong>Difference:</strong> {_e(item.get('difference'))}</p>")
            assessments.append(f"<section class='assessment'><h3>{_e(item.get('field'))}</h3>{scores}{_explanation(item)}</section>")
        record_heading = ("<h2>Your task</h2>" if single_task else
                          f"<h2>Record {_e(row.get('record'))} · {_e(row.get('status'))}</h2><p class='muted'>Source line {_e(row.get('source_line'))}</p>")
        cards.append(
            f"<article>{record_heading}"
            + (f"<p class='notice'>{_e(row.get('error') or row.get('skip_reason'))}</p>" if row.get("error") or row.get("skip_reason") else "")
            + "".join(assessments)
            + f"<details><summary>Original question, answer, and reference</summary><h3>Question / prompt</h3><pre>{_e(row.get('prompt'))}</pre><h3>Answer</h3><pre>{_e(row.get('response'))}</pre>"
            + (f"<h3>Supplied reference</h3><pre>{_e(row.get('reference'))}</pre>" if row.get("reference") is not None else "")
            + ("" if single_task else f"<h3>Original ratings</h3><pre>{_e(row.get('original_ratings', {}))}</pre>")
            + "</details></article>"
        )
    counts = report.get("counts", {})
    labels = {"scored": "Criteria scored", "insufficient_evidence": "Insufficient evidence"} if single_task else COMPARISONS
    metrics = "".join(f"<div><span>{_e(label)}</span><strong>{_e(counts.get(key, 0))}</strong></div>" for key, label in labels.items())
    limitations = "".join(f"<li>{_e(line)}</li>" for line in report.get("limitations", []))
    rubrics = "".join(f"<section><h3>{_e(item.get('field'))}</h3><p>Scale: {_e(item.get('min'))}–{_e(item.get('max'))} · {'Whole numbers' if item.get('integer') else 'Numeric scores'}</p><pre>{_e(item.get('description'))}</pre></section>" for item in report.get("attributes", []))
    title = "Task evaluation" if single_task else "Answer review"
    if primary:
        title += " · Second opinion"
    if single_task:
        intro = f"<p>{_e(report.get('model'))} evaluated the pasted response using the supplied rubric.</p>" if any(row.get("assessments") for row in rows) else "<p>No criterion scores were returned for this evaluation.</p>"
        metric_note = ""
        source_details = f"<p>Rubric file &nbsp; {_e(source['rubric_filename'])}</p>" if source.get("rubric_filename") else ""
        tolerance = ""
    else:
        intro = (f"<p><strong>{_e(report.get('processed_count', len(rows)))} of {_e(report.get('selected_count', len(rows)))} selected records processed</strong>; {_e(report.get('pending_count', 0))} not processed. Source dataset: {_e(report.get('total_records'))} records.</p>"
                 f"<p>{_e(report.get('selection_note'))}</p><p>These are model-assisted second opinions, not verified ground truth. Source ratings were hidden from the model; comparison happens afterward. Your original file and ratings were not changed.</p>")
        scope = ("Whole-dataset scope includes every record; skipped, failed, and pending records have no completed assessment."
                 if report.get("options", {}).get("selection") == "all"
                 else "No dataset-wide quality conclusion follows from this sample.")
        metric_note = f"<p class='muted'>Counts above refer to attribute judgments. One record may contain several judgments. {scope}</p>"
        source_details = f"<p>File fingerprint: <code>{_e(source.get('sha256'))}</code></p>"
        tolerance = f"<p>Comparison tolerance: {_e(report.get('options', {}).get('tolerance'))}</p>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>{REPORT_PALETTE}
{REVIEW_STYLES}
</style></head><body><main><p class="eyebrow">DATA QUALITY INSPECTOR</p><h1>{title}</h1>""" + (
        f"<p>{_e(source.get('filename', 'Pasted task' if single_task else 'Dataset'))}</p><section class='intro'><p class='badge'>Run: {_e(report.get('run_status', 'completed'))}</p>{intro}"
        + (f"<p class='notice'>{_e(report['error'])}</p>" if report.get("error") else "")
        + (f"<p class='notice'>{_e(report['usage_note'])}</p>" if report.get("usage_complete") is False and report.get("usage_note") else "")
        + (f"<p>These counts compare the suggestions from {_e(report.get('model'))} with the original dataset ratings.</p>" if primary and not single_task else "")
        + f"<div class='metrics'>{metrics}</div>{metric_note}</section>"
        + (_comparison_html(primary, report) if primary else "")
        + f"<details class='rubrics'><summary>Rules, model, and source details</summary><p>Provider: {_e(report.get('provider'))} · Requested model: {_e(report.get('model'))}</p>"
        + f"<p>Models reported by the provider: {_e(', '.join(report.get('actual_models', [])) or 'Not reported')}</p><p>Reported token usage: {_e(report.get('usage', {}))}</p><p>{_e(report.get('usage_note', 'Usage information was not recorded.'))}</p>"
        + f"<p>Reviewed: {_e(report.get('completed_at'))} · Inspector {_e(source.get('app_version'))}</p>{source_details}"
        + f"<p>Rubric version: {_e(report.get('rubric_version'))} · Fingerprint: <code>{_e(report.get('rubric_sha256'))}</code></p>{tolerance}{rubrics}"
        + (f"<h3>Reviewer instructions used for this run</h3><p>Instruction fingerprint: <code>{_e(report.get('instructions_sha256'))}</code></p><pre>{_e(report['reviewer_instructions'])}</pre>" if report.get('reviewer_instructions') else "")
        + "</details>"
        + "".join(cards)
        + f"<section class='intro'><h2>Limits of this review</h2><ul>{limitations}</ul><p>A quoted passage is checked against the original record; that verifies the excerpt, not the model's interpretation or external facts. Review important disagreements against the source rubric and trusted references.</p></section>"
        + "<p><a href='answer-review.json'>Complete JSON report</a> · <a href='answer-review.csv'>Review CSV</a></p></main></body></html>"
    )


def export_review(report, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "answer-review.json").write_text(dumps_json(report) + "\n", encoding="utf-8")
    (destination / "answer-review.html").write_text(render_html(report), encoding="utf-8")
    fields = ("record", "source_line", "record_status", "field", "original_score", "suggested_score", "difference", "comparison", "rationale", "evidence", "limitations", "prompt", "response", "reference", "error")
    if report.get("review_mode") == "single_task":
        fields = ("record_status", "field", "min", "max", "suggested_score", "score_status", "rationale", "evidence", "limitations", "prompt", "response", "reference", "error")
    criteria = {item["field"]: item for item in report.get("attributes", [])}
    primary = report.get("primary_review")
    if primary:
        fields += ("second_opinion_model", "primary_model", "primary_suggested_score", "primary_comparison",
                   "primary_rationale", "primary_evidence", "primary_limitations")
    with (destination / "answer-review.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        rows = report.get("rows", [])
        if primary and not rows:
            rows = [{"record": primary["record"], "status": "not_processed", "error": report.get("error")}]
        for row in rows:
            if primary and row["record"] == primary["record"]:
                pairs = _paired_assessments(primary, row)
            else:
                pairs = [(item["field"], None, item) for item in row.get("assessments", [])]
            for field, first, second in pairs or [(None, None, None)]:
                combined = {**row, **(second or {}), "field": field, "record_status": row.get("status"),
                            "error": row.get("error") or row.get("skip_reason")}
                if report.get("review_mode") == "single_task":
                    rule = criteria.get(field, {})
                    combined.update(min=rule.get("min"), max=rule.get("max"), score_status=combined.get("comparison"))
                if primary:
                    combined.update(primary_model=primary.get("model"), second_opinion_model=report.get("model"))
                if first:
                    combined["original_score"] = first.get("original_score")
                    for key in ("suggested_score", "comparison", "rationale", "evidence", "limitations"):
                        combined["primary_" + key] = first.get(key)
                writer.writerow([csv_value(combined.get(field) if combined.get(field) is not None else "") for field in fields])
    with zipfile.ZipFile(destination / "answer-review.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(destination / name, arcname=name)
