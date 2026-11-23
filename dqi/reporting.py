"""Readable, self-contained reports built from an inspection result dictionary."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
from html import escape

from .json_utils import dumps_json
from .report_appearance import REPORT_PALETTE


PREVIEW_LIMIT = 20
VALUE_LIMIT = 160
CONTEXT_LIMIT = 1200
STATUSES = {
    "passed": ("Passed", "All selected structural checks passed."),
    "issues_found": ("Findings detected", "Some records did not satisfy the selected rules."),
    "incomplete": ("Incomplete", "Some selected checks could not be evaluated."),
    "empty_dataset": ("Empty dataset", "No data records were available to inspect."),
    "no_checks_selected": ("No checks selected", "No validation rules were enabled for this run."),
}


def _text(value) -> str:
    return str(value).encode("utf-8", errors="backslashreplace").decode("utf-8")


def _escape(value) -> str:
    return escape(_text(value), quote=True)


def _count(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _number(value, *, mean=False) -> str:
    if value is None:
        return "—"
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
        if not number.is_finite():
            return _text(value)
        if not number:
            return "0.00" if mean else "0"
        # Keep an unbounded custom rating scale from producing enormous labels.
        if abs(number.adjusted()) > 8:
            return format(number, ".3E")
        if mean:
            return format(number, ".2f")
        return format(number, "f").rstrip("0").rstrip(".") if number.as_tuple().exponent < 0 else str(number)
    except (InvalidOperation, TypeError, ValueError):
        return _text(value)


def _source_name(source_name) -> str:
    return _text(source_name).replace("\\", "/").rsplit("/", 1)[-1] or "Dataset"


def _rule_labels(rules) -> list[str]:
    labels = []
    if rules.get("required"):
        labels.append("required")
    if rules.get("numeric"):
        labels.append("finite number")
    if rules.get("integer"):
        labels.append("whole number")
    bounds = rules.get("range")
    if bounds is not None and len(bounds) == 2:
        labels.append(f"{_number(bounds[0])}–{_number(bounds[1])} inclusive")
    return labels or ["no rating rules enabled"]


def _common_checks(report) -> list[str]:
    config = report.get("config", {})
    columns = config.get("columns", {})
    required = [columns.get(key, key) for key in config.get("required", []) if key != "rating"]
    checks = []
    if required:
        checks.append("Required fields: " + ", ".join(map(_text, required)) + ".")
    if config.get("unique_id"):
        checks.append(f"Unique identifiers: {_text(columns.get('evaluation_id', 'evaluation_id'))}.")
    return checks


def _preview(finding) -> tuple[str, bool]:
    if not finding.get("field_present", True):
        return "(field absent)", False
    value = finding.get("value")
    rendered = _text(value) if isinstance(value, str) else dumps_json(value, indent=None)
    if value == "":
        rendered = '"" (empty string)'
    return (rendered[:VALUE_LIMIT] + "…", True) if len(rendered) > VALUE_LIMIT else (rendered, False)


def _interpretation(report) -> list[str]:
    notes = [
        "This report checks data structure and configured rules. It does not establish that an AI response or a human evaluation is correct.",
        "Statistics and score distributions use only nonmissing numeric values that satisfy all selected rules for that attribute. Excluded values remain visible in the counts above.",
    ]
    fields = {summary.get("field") for summary in report.get("rating_summaries", [])}
    if fields & {"complexity", "verbosity"}:
        notes.append("Higher complexity or verbosity is not inherently better. Their distributions describe depth and detail; they are not quality rankings.")
    notes.append("Record counts refer to dataset rows. Rating-value counts count each selected attribute separately. A record can have more than one finding, and failure categories can overlap.")
    return notes


def _findings_html(rows, heading, total, csv_name):
    if not rows:
        return ""
    table_rows, truncated = [], False
    for finding in rows[:PREVIEW_LIMIT]:
        value, clipped = _preview(finding)
        truncated |= clipped
        source_line = finding.get("source_line")
        location = f"Record {_text(finding.get('record', '—'))}"
        if source_line is not None:
            location += f" · line {_text(source_line)}"
        table_rows.append(
            "<tr>"
            f"<td class='location'>{_escape(location)}</td>"
            f"<td><code>{_escape(finding.get('field', ''))}</code><span class='cell-note'>{_escape(finding.get('rule', ''))}</span></td>"
            f"<td><code class='value'>{_escape(value)}</code><span class='cell-note'>{_escape(finding.get('value_type', ''))}</span></td>"
            f"<td>{_escape(finding.get('reason', ''))}</td>"
            "</tr>"
        )
    preview_note = f"Showing {min(len(rows), PREVIEW_LIMIT):,} of {_count(total):,}."
    if truncated:
        preview_note += f" Long values are shortened to {VALUE_LIMIT} characters in this preview."
    return (
        f"<section><div class='section-heading'><h2>{_escape(heading)}</h2>"
        f"<a href='{_escape(csv_name)}'>Open complete CSV</a></div>"
        f"<p class='subtle'>{_escape(preview_note)}</p>"
        "<div class='table-wrap'><table class='findings'><thead><tr>"
        "<th scope='col'>Location</th><th scope='col'>Field / rule</th>"
        "<th scope='col'>Observed value</th><th scope='col'>Reason</th>"
        "</tr></thead><tbody>" + "".join(table_rows) + "</tbody></table></div></section>"
    )


def _audit_outcome(audit):
    if _count(audit.get("review_record_count")) or (audit.get("coverage") or {}).get("gaps"):
        return "Review needed", "attention"
    if not any(item.get("status") == "completed" for item in audit.get("check_status", [])):
        return "Audit checks not assessed", "neutral"
    return "No review flags under selected rules", "neutral"


def _context(value):
    if value is None:
        return "(not provided)"
    text = _text(value) if isinstance(value, str) else dumps_json(value, indent=None)
    return text[:CONTEXT_LIMIT] + "…" if len(text) > CONTEXT_LIMIT else text


def _category_label(value):
    return dumps_json(value, indent=None) if isinstance(value, str) and value != value.strip() else _text(value)


def _audit_origin(audit):
    origin = audit.get("provenance", {}).get("rule_origin", "custom")
    return {"helpsteer2": "HelpSteer2 preset", "standard": "Standard evaluation preset", "custom": "Custom rules"}.get(origin, _text(origin))


def _audit_thresholds(audit):
    thresholds = audit.get("options", {}).get("review_thresholds")
    if not isinstance(thresholds, dict):
        thresholds = audit.get("provenance", {}).get("review_thresholds", {})
    return thresholds if isinstance(thresholds, dict) else {}


def _audit_provenance_notes(audit):
    provenance = audit.get("provenance", {})
    return [provenance[key] for key in ("structural_rules", "review_thresholds") if isinstance(provenance.get(key), str)]


def _audit_check_line(item):
    name = _text(item.get("check", "check")).replace("_", " ")
    status = _text(item.get("status", "not_assessed")).replace("_", " ")
    counts = []
    for key in ("eligible_records", "skipped_records", "eligible_values", "skipped_values", "matched_values"):
        if key in item:
            counts.append(f"{_count(item[key]):,} {key.replace('_', ' ')}")
    details = "; ".join(counts + ([_text(item["reason"])] if item.get("reason") else []))
    return f"{name}: {status}" + (f" — {details}" if details else "")


def _audit_location(row):
    location = f"Record {_text(row.get('record', '—'))}"
    if row.get("source_line") is not None:
        location += f" · line {_text(row['source_line'])}"
    return location


def _review_reason_label(reason):
    names = {"data_error": "Data validation error", "skipped_check": "Skipped validation check", "duplicate": "Exact repeated pair", "conflicting_rating": "Conflicting ratings", "low_rating": "Source rating meets review threshold"}
    kind = reason.get("kind", "review_reason")
    label = names.get(kind, _text(kind).replace("_", " ").capitalize())
    return label + (f" · {_text(reason['field'])}" if reason.get("field") else "")


def _simple_report_html(report):
    sections = []
    findings = report.get("findings") or report.get("skipped_checks") or []
    if findings:
        first = findings[0]
        sections.append(
            "<section class='scope'><h2>First check requiring attention</h2>"
            f"<p>{_escape(_audit_location(first))} · <code>{_escape(first.get('field', ''))}</code></p>"
            f"<p>{_escape(first.get('reason') or '')}</p></section>"
        )
    audit = report.get("audit")
    if audit is None:
        return "".join(sections)

    count = _count(audit.get("review_record_count"))
    outcome, _ = _audit_outcome(audit)
    title = f"{count:,} {'record' if count == 1 else 'records'} to review" if count else outcome
    content = [f"<section class='scope'><h2>{_escape(title)}</h2>"]
    thresholds = _audit_thresholds(audit)
    counts = Counter()
    for row in audit.get("review_queue", []):
        labels = []
        for reason in row.get("reasons", []):
            field = reason.get("field")
            if reason.get("kind") == "low_rating" and field in thresholds:
                label = f"{field} ≤ {dumps_json(thresholds[field], indent=None)}"
            else:
                label = _review_reason_label(reason)
            labels.append(label)
        # A record may have several failed checks on the same field.
        counts.update(dict.fromkeys(labels, 1))
    if counts:
        content.append("<p>Most common reasons for review</p><ul>")
        for label, n in counts.most_common(3):
            content.append(f"<li>{_escape(label)} · {n:,} {'record' if n == 1 else 'records'}</li>")
        content.append("</ul>")
        notes = []
        if thresholds:
            notes.append("Score thresholds use the ratings in the uploaded file.")
        if len(counts) > 1:
            notes.append("One record can count toward more than one reason.")
        if notes:
            content.append(f"<p class='subtle'>{' '.join(notes)}</p>")
    if count:
        content.append("<p>Use Detailed to see each example’s prompt, response, source ratings, and reasons for review.</p>")
    gaps = (audit.get("coverage") or {}).get("gaps") or []
    if gaps:
        gap = gaps[0]
        content.append(f"<p>{len(gaps):,} {'group falls' if len(gaps) == 1 else 'groups fall'} below the coverage target. "
                       f"Group <code>{_escape(_category_label(gap.get('value', '')))}</code> has "
                       f"{_count(gap.get('count')):,} records against a minimum of {_count(gap.get('minimum')):,}.</p>")
    content.append("</section>")
    return "".join(sections + content)


def _audit_example_heading(row):
    heading = _audit_location(row)
    if row.get("field") is not None and "value" in row:
        heading += f" · {_text(row['field'])}: {_context(row['value'])}"
    return heading


def _conflict_evidence(reason, conflicts):
    if reason.get("kind") != "conflicting_rating":
        return []
    group = conflicts.get(reason.get("group_id"), {})
    lines = []
    for difference in group.get("differences", []):
        if difference.get("field") != reason.get("field"):
            continue
        for bucket in difference.get("values", []):
            records = bucket.get("records", [])
            preview = ", ".join(map(_text, records[:PREVIEW_LIMIT]))
            score = dumps_json(bucket.get("value"), indent=None)
            line = f"Source score {score}: records {preview} ({len(records):,} total)."
            if len(records) > PREVIEW_LIMIT:
                line += f" Showing the first {PREVIEW_LIMIT}; full membership is in report.json."
            lines.append(line)
    return lines


def _audit_record_html(row, *, positive=False, conflicts=None):
    reasons = row.get("reasons", [])
    explanation = ""
    if positive:
        explanation = f"<p>{_escape(row.get('selection_reason', 'Selected from existing recorded ratings.'))}</p>"
    else:
        reason_items = []
        for reason in reasons:
            evidence = _conflict_evidence(reason, conflicts or {})
            evidence_html = ""
            if evidence:
                evidence_html = f"<p>Recorded scores for <code>{_escape(reason.get('field', ''))}</code>:</p><ul>" + "".join(f"<li>{_escape(line)}</li>" for line in evidence) + "</ul>"
            reason_items.append(f"<li><strong>{_escape(_review_reason_label(reason))}</strong><br>{_escape(reason.get('reason', ''))}{evidence_html}<br><strong>Action:</strong> {_escape(reason.get('action', 'Review this record in context.'))}</li>")
        explanation = "<h4>Why review and next actions</h4><ul>" + "".join(reason_items) + "</ul>"
    return (
        f"<article class='audit-record'><h3>{_escape(_audit_example_heading(row) if positive else _audit_location(row))}"
        + ("" if positive else f" <span class='cell-note'>Priority: {_escape(row.get('priority', 'medium'))}</span>")
        + "</h3>" + explanation
        + f"<h4>Prompt</h4><pre>{_escape(_context(row.get('prompt')))}</pre>"
        + f"<h4>Response</h4><pre>{_escape(_context(row.get('response')))}</pre>"
        + f"<h4>Original ratings</h4><pre>{_escape(_context(row.get('ratings', {})))}</pre></article>"
    )


def _audit_html(audit):
    outcome, status_class = _audit_outcome(audit)
    thresholds = _audit_thresholds(audit)
    threshold_html = "".join(f"<li>{_escape(field)} ≤ {_escape(dumps_json(bound, indent=None))} (inclusive)</li>" for field, bound in thresholds.items())
    if not threshold_html:
        threshold_html = "<li>No score review thresholds selected.</li>"
    checks = "".join(f"<li>{_escape(_audit_check_line(item))}</li>" for item in audit.get("check_status", []))
    limitations = "".join(f"<li>{_escape(note)}</li>" for note in audit.get("limitations", []))
    coverage = audit.get("coverage")
    if coverage is None:
        coverage_html = "<p>Category coverage: not assessed; no grouping rule selected.</p>"
    else:
        groups = "".join(
            f"<tr><td><code class='value'>{_escape(_category_label(group.get('value', '')))}</code></td><td>{_count(group.get('count')):,}</td>"
            f"<td>{'Expected' if group.get('expected') else 'Observed'}</td>"
            f"<td>{'Below minimum' if group.get('below_minimum') else 'Meets minimum'}</td></tr>"
            for group in coverage.get("groups", [])
        )
        gaps = "".join(f"<li><code class='value'>{_escape(_category_label(gap.get('value', '')))}</code>: {_count(gap.get('count')):,} records; minimum {_count(gap.get('minimum')):,} ({_escape(_text(gap.get('kind', '')).replace('_', ' '))}).</li>" for gap in coverage.get("gaps", []))
        coverage_html = (
            f"<p>Group field: <code>{_escape(coverage.get('field', ''))}</code>. Minimum: {_count(coverage.get('minimum')):,} records per group. "
            f"Missing group values: {_count(coverage.get('missing_records')):,}; unsupported values: {_count(coverage.get('unsupported_records')):,}.</p>"
            + ("<ul>" + gaps + "</ul>" if gaps else "<p>No gaps under the selected coverage rule.</p>")
            + "<div class='table-wrap'><table><thead><tr><th>Group</th><th>Records</th><th>Coverage scope</th><th>Minimum check</th></tr></thead><tbody>" + groups + "</tbody></table></div>"
        )
    queue = audit.get("review_queue", [])
    conflicts = {group.get("group_id"): group for group in audit.get("conflict_groups", [])}
    examples = audit.get("positively_rated_examples", [])[:3]
    source = audit.get("source")
    source_html = ""
    if source:
        source_html = (
            f"<p class='subtle'>Source: {_escape(_source_name(source.get('filename', 'Dataset')))} · "
            f"Inspector {_escape(source.get('app_version', ''))} · inspected {_escape(source.get('inspected_at', ''))}.<br>"
            f"Original uploaded bytes SHA-256: <code>{_escape(source.get('sha256', ''))}</code></p>"
        )
    return (
        f"<section class='scope audit'><h2>Audit review</h2><p><span class='status {status_class}'>{_escape(outcome)}</span></p>"
        "<p class='subtle'>Data validation and audit review are separate outcomes. Review flags use existing data and selected rules; they are not new AI judgments about answer quality.</p>"
        f"<p>{_count(audit.get('review_record_count')):,} records queued for review. "
        f"Exact duplicate groups: {_count(audit.get('duplicate_group_count')):,} ({_count(audit.get('duplicate_record_count')):,} records). "
        f"Conflicting-rating groups: {_count(audit.get('conflict_group_count')):,} ({_count(audit.get('conflict_record_count')):,} records).</p>"
        + (f"<p>Source-score threshold matches: {_count(audit.get('threshold_match_count')):,} attribute values. Multiple matches can belong to the same queued record.</p>" if "threshold_match_count" in audit else "")
        +
        f"<h3>Rule provenance</h3><p>Selected rule origin: {_escape(_audit_origin(audit))}.</p>"
        + source_html +
        "<p>Structural rules are listed in Checks run. Score thresholds below only select records for human review.</p>"
        + "".join(f"<p class='subtle'>{_escape(note)}</p>" for note in _audit_provenance_notes(audit)) +
        f"<h4>Explicit review thresholds</h4><ul>{threshold_html}</ul>"
        f"<p>{_escape(_context(audit.get('provenance', {}).get('rating_evidence', 'Ratings are taken from the uploaded dataset.')))}</p>"
        f"<h3>Checks completed and not assessed</h3><ul>{checks}</ul>"
        f"<h3>Category coverage</h3>{coverage_html}"
        + (f"<h3>Limits of this audit</h3><ul>{limitations}</ul>" if limitations else "")
        + "<div class='section-heading'><h3>Records to review</h3><a href='review-queue.csv'>Open complete review queue CSV</a></div>"
        + f"<p class='subtle'>Showing {min(len(queue), PREVIEW_LIMIT):,} of {_count(audit.get('review_record_count')):,}. Context is shortened after {CONTEXT_LIMIT:,} characters. Complete context and original ratings: review-queue.csv and report.json; full actions: report.json.</p>"
        + ("".join(_audit_record_html(row, conflicts=conflicts) for row in queue[:PREVIEW_LIMIT]) if queue else "<p>No records were queued. Review coverage gaps and checks not assessed above.</p>")
        + ("<h3>Examples with high existing ratings</h3><p class='subtle'>These illustrate ratings already present in the source. They are not endorsements or fresh AI evaluations.</p>" + "".join(_audit_record_html(row, positive=True) for row in examples) if examples else "")
        + "</section>"
    )


def _audit_text(audit):
    outcome, _ = _audit_outcome(audit)
    lines = ["", "AUDIT REVIEW", outcome,
             "Data validation and audit review are separate outcomes. Review flags use existing data and selected rules; they are not new AI judgments about answer quality.",
             f"Records queued for review: {_count(audit.get('review_record_count')):,}",
             f"Exact duplicate groups: {_count(audit.get('duplicate_group_count')):,}; records: {_count(audit.get('duplicate_record_count')):,}",
             f"Conflicting-rating groups: {_count(audit.get('conflict_group_count')):,}; records: {_count(audit.get('conflict_record_count')):,}",
             f"Selected rule origin: {_audit_origin(audit)}.",
             "Structural rules are listed in CHECKS RUN. Review thresholds select records for human review only."]
    lines.extend(_audit_provenance_notes(audit))
    source = audit.get("source")
    if source:
        lines.extend([
            f"Source: {_source_name(source.get('filename', 'Dataset'))}; Inspector {_text(source.get('app_version', ''))}; inspected {_text(source.get('inspected_at', ''))}.",
            f"Original uploaded bytes SHA-256: {_text(source.get('sha256', ''))}",
        ])
    if "threshold_match_count" in audit:
        lines.append(f"Source-score threshold matches: {_count(audit.get('threshold_match_count')):,} attribute values; multiple matches can belong to the same queued record.")
    thresholds = _audit_thresholds(audit)
    lines.extend([f"Review threshold: {_text(field)} <= {dumps_json(bound, indent=None)} (inclusive)." for field, bound in thresholds.items()] or ["No score review thresholds selected."])
    lines.append(_context(audit.get("provenance", {}).get("rating_evidence", "Ratings are taken from the uploaded dataset.")))
    lines.extend(["", "CHECKS COMPLETED AND NOT ASSESSED", *[_audit_check_line(item) for item in audit.get("check_status", [])]])
    coverage = audit.get("coverage")
    if coverage is None:
        lines.append("Category coverage: not assessed; no grouping rule selected.")
    else:
        lines.extend(["", "CATEGORY COVERAGE", f"Group field: {_text(coverage.get('field', ''))}; minimum {_count(coverage.get('minimum')):,} records per group.",
                      f"Missing group values: {_count(coverage.get('missing_records')):,}; unsupported values: {_count(coverage.get('unsupported_records')):,}."])
        for group in coverage.get("groups", []):
            lines.append(f"- {_category_label(group.get('value', ''))}: {_count(group.get('count')):,}; {'below minimum' if group.get('below_minimum') else 'meets minimum'}.")
        for gap in coverage.get("gaps", []):
            lines.append(f"Coverage gap: {_category_label(gap.get('value', ''))}; {_count(gap.get('count')):,} records; minimum {_count(gap.get('minimum')):,}; {_text(gap.get('kind', '')).replace('_', ' ')}.")
        if not coverage.get("gaps"):
            lines.append("No gaps under the selected coverage rule.")
    lines.extend(["", "LIMITS OF THIS AUDIT", *map(_text, audit.get("limitations", []))])
    queue = audit.get("review_queue", [])
    conflicts = {group.get("group_id"): group for group in audit.get("conflict_groups", [])}
    lines.extend(["", "RECORDS TO REVIEW", f"Showing {min(len(queue), PREVIEW_LIMIT):,} of {_count(audit.get('review_record_count')):,}. Context is shortened after {CONTEXT_LIMIT:,} characters. Complete context and original ratings: review-queue.csv and report.json; full actions: report.json."])
    for row in queue[:PREVIEW_LIMIT]:
        lines.append(f"\n{_audit_location(row)}; priority: {_text(row.get('priority', 'medium'))}")
        for reason in row.get("reasons", []):
            lines.append(f"Why review ({_review_reason_label(reason)}): {_text(reason.get('reason', ''))} Action: {_text(reason.get('action', 'Review this record in context.'))}")
            evidence = _conflict_evidence(reason, conflicts)
            if evidence:
                lines.append(f"Recorded scores for {_text(reason.get('field', ''))}:")
                lines.extend(f"  - {line}" for line in evidence)
        lines.extend(f"{label}: {_context(row.get(key))}" for label, key in (("Prompt", "prompt"), ("Response", "response"), ("Original ratings", "ratings")))
    examples = audit.get("positively_rated_examples", [])[:3]
    if examples:
        lines.extend(["", "EXAMPLES WITH HIGH EXISTING RATINGS", "These illustrate ratings already present in the source. They are not endorsements or fresh AI evaluations."])
        for row in examples:
            lines.extend([_audit_example_heading(row), _text(row.get("selection_reason", "Selected from existing recorded ratings."))])
            lines.extend(f"{label}: {_context(row.get(key))}" for label, key in (("Prompt", "prompt"), ("Response", "response"), ("Original ratings", "ratings")))
    return lines


def render_html(report: dict, source_name: str = "") -> str:
    """Render a readable HTML document with no external resources or scripting."""
    status = _text(report.get("status", "unknown"))
    status_label, status_message = STATUSES.get(status, ("Unknown status", "See the JSON report for the recorded outcome."))
    audit = report.get("audit")
    if audit is not None:
        status_label = "Data validation: " + status_label
    status_class = "success" if status == "passed" else "attention" if status in {"issues_found", "incomplete"} else "neutral"
    summaries = report.get("rating_summaries", [])
    source = _source_name(source_name)
    total_scores = sum(_count(summary.get("counts", {}).get("total")) for summary in summaries)
    metrics = [("Records inspected", _count(report.get("total_records")))]
    if summaries:
        metrics.append(("Rating values examined", total_scores))
    metrics.extend([
        ("Findings", _count(report.get("finding_count"))),
        ("Affected records", _count(report.get("affected_records"))),
        ("Skipped checks", _count(report.get("skipped_check_count"))),
    ])
    metrics_html = "".join(f"<div class='metric'><dt>{_escape(label)}</dt><dd>{count:,}</dd></div>" for label, count in metrics)
    checks = _common_checks(report)
    common_html = "".join(f"<li>{_escape(check)}</li>" for check in checks)
    if not checks:
        common_html = "<li>No shared required-field or uniqueness checks were selected.</li>"
    if summaries:
        common_html += "<li>Each rating attribute uses the checks listed below. Shared field checks run once per record.</li>"
    elif report.get("config", {}).get("numeric_rating") or report.get("config", {}).get("rating_range") is not None or "rating" in report.get("config", {}).get("required", []):
        config = report.get("config", {})
        field = config.get("columns", {}).get("rating", "rating")
        rules = {"required": "rating" in config.get("required", []), "numeric": config.get("numeric_rating"), "range": config.get("rating_range")}
        common_html += f"<li>{_escape(field)}: {_escape(', '.join(_rule_labels(rules)))}.</li>"
    attribute_html = ""
    distribution_html = ""
    if summaries:
        rows, panels = [], []
        for summary in summaries:
            field = _text(summary.get("field", "Rating"))
            counts = summary.get("counts", {})
            stats = summary.get("statistics", {})
            rows.append(
                f"<tr><th scope='row'><code>{_escape(field)}</code><span class='cell-note'>{_escape(' · '.join(_rule_labels(summary.get('rules', {}))))}</span></th>"
                f"<td class='number'>{_count(counts.get('valid')):,} / {_count(counts.get('total')):,}</td>"
                + "".join(f"<td class='number'>{_count(counts.get(key)):,}</td>" for key in ("missing", "invalid_numeric", "non_integer", "out_of_range", "findings", "skipped_checks"))
                + "</tr>"
            )
            denominator = _count(stats.get("count"))
            if denominator:
                distribution_rows = []
                for bin_ in summary.get("distribution", []):
                    count = _count(bin_.get("count"))
                    percent = count / denominator * 100
                    width = min(100.0, max(0.0, percent))
                    distribution_rows.append(
                        "<tr>"
                        f"<th scope='row'>{_escape(_number(bin_.get('value')))}</th>"
                        f"<td class='bar-cell'><div class='bar-track' aria-hidden='true'><div class='bar-fill' style='width:{width:.3f}%'></div></div></td>"
                        f"<td class='number'>{count:,}</td><td class='number'>{percent:.1f}%</td>"
                        "</tr>"
                    )
                distribution = (
                    "<table class='distribution'><thead><tr><th scope='col'>Score</th><th scope='col'><span class='sr-only'>Distribution</span></th>"
                    "<th scope='col' class='number'>Count</th><th scope='col' class='number'>Share</th></tr></thead><tbody>"
                    + "".join(distribution_rows) + "</tbody></table>"
                )
            else:
                distribution = "<p class='empty-note'>No valid numeric scores are available for this attribute.</p>"
            panels.append(
                f"<article class='distribution-panel'><h3>{_escape(field)}</h3>"
                f"<p class='subtle'>{denominator:,} valid scores</p>"
                "<dl class='stats'>"
                f"<div><dt>Minimum</dt><dd>{_escape(_number(stats.get('minimum')))}</dd></div>"
                f"<div><dt>Maximum</dt><dd>{_escape(_number(stats.get('maximum')))}</dd></div>"
                f"<div><dt>Mean</dt><dd>{_escape(_number(stats.get('mean'), mean=True))}</dd></div></dl>"
                + (f"<p class='subtle'>{_escape(stats['mean_note'])}</p>" if stats.get("mean_note") else "")
                + distribution + "</article>"
            )
        attribute_html = (
            "<section><h2>Checks by rating attribute</h2><p class='subtle'>Counts include diagnostics and enabled-rule failures. Optional missing values or disabled numeric checks can produce counts without findings. Categories can overlap.</p>"
            "<div class='table-wrap'><table class='attributes'><thead><tr><th scope='col'>Attribute / selected checks</th>"
            "<th scope='col'>Valid / total</th><th scope='col'>Missing</th><th scope='col'>Nonnumeric</th>"
            "<th scope='col'>Noninteger</th><th scope='col'>Outside range</th><th scope='col'>Findings</th><th scope='col'>Skipped</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div></section>"
        )
        distribution_html = (
            "<section><h2>Score distributions</h2><p class='subtle'>Shares and summary statistics use valid scores only. Means are displayed to two decimal places.</p>"
            "<div class='distribution-grid'>" + "".join(panels) + "</div></section>"
        )
    notes_html = "".join(f"<li>{_escape(note)}</li>" for note in _interpretation(report))
    findings_html = _findings_html(report.get("findings", []), "Findings", report.get("finding_count", 0), "findings.csv")
    skipped_html = _findings_html(report.get("skipped_checks", []), "Checks requiring attention", report.get("skipped_check_count", 0), "skipped-checks.csv")
    audit_html = _audit_html(audit) if audit is not None else ""
    simple_html = _simple_report_html(report)
    review_download = '<a href="review-queue.csv">Review queue CSV</a>' if audit is not None else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'">
<title>Inspection summary — {_escape(source)}</title>
<style>
{REPORT_PALETTE}
*{{box-sizing:border-box}}body{{margin:0;background:var(--canvas);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1160px;margin:0 auto;padding:44px 28px 32px}}h1,h2,h3,p,dl{{margin:0}}h1{{font-size:34px;line-height:1.2;letter-spacing:-.7px;font-weight:650}}h2{{font-size:20px;font-weight:600;margin-bottom:8px}}h3{{font-size:16px;font-weight:600;overflow-wrap:anywhere}}
.eyebrow{{font-size:12px;letter-spacing:1.4px;text-transform:uppercase;color:var(--muted);margin-bottom:10px}}.source{{margin-top:12px;overflow-wrap:anywhere}}.source code{{font-size:14px}}.subtle,.cell-note{{color:var(--muted);font-size:13px}}.status-row{{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-top:22px}}.status{{font-size:13px;font-weight:600;padding:5px 11px;border-radius:999px}}.success{{background:var(--positive-bg);color:var(--positive-ink)}}.attention{{background:var(--attention-bg);color:var(--attention-ink)}}.neutral{{background:var(--neutral-bg);color:var(--neutral-ink)}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:25px 0 28px}}.metric{{background:var(--surface);padding:17px 18px;border:1px solid var(--line);border-radius:10px}}.metric dt{{font-size:12px;color:var(--muted)}}.metric dd{{margin:4px 0 0;font-size:28px;line-height:1.2;font-weight:600;font-variant-numeric:tabular-nums}}
.audit h3{{margin-top:22px}}.audit h4{{margin:15px 0 6px}}.audit p{{margin-top:10px}}.audit-record{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:18px;margin-top:14px;break-inside:avoid}}.audit-record h3{{margin-top:0}}.audit pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;margin:6px 0}}
section{{margin-top:28px}}.scope{{border-top:1px solid var(--line);padding-top:22px}}ul{{padding-left:20px;margin:10px 0 0}}li+li{{margin-top:6px}}.table-wrap{{overflow-x:auto;margin-top:14px;background:var(--surface);border:1px solid var(--line);border-radius:10px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:12px 14px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}}thead th{{font-size:12px;font-weight:600;color:var(--muted);background:var(--inset)}}tbody tr:last-child td,tbody tr:last-child th{{border-bottom:0}}tbody th{{font-weight:500}}.attributes{{min-width:860px}}.attributes th:first-child{{width:30%}}.number{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}.cell-note{{display:block;margin-top:4px;font-weight:400;max-width:330px}}code{{font:inherit;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}}.distribution-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:16px}}.distribution-panel{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:20px;break-inside:avoid}}.stats{{display:flex;gap:32px;margin:16px 0 12px}}.stats dt{{font-size:12px;color:var(--muted)}}.stats dd{{margin:2px 0 0;font-size:18px;font-weight:500;font-variant-numeric:tabular-nums}}.distribution th,.distribution td{{padding:7px 0;vertical-align:middle;border:0}}.distribution thead th{{background:none;font-size:11px;padding-bottom:10px}}.distribution .bar-cell{{width:100%;padding:7px 14px}}.distribution .number{{padding-left:14px}}.bar-track{{height:9px;background:var(--track);border-radius:3px;overflow:hidden}}.bar-fill{{height:100%;background:var(--accent);border-radius:3px}}.empty-note{{font-size:13px;color:var(--muted);padding:16px 0}}.section-heading{{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap}}a{{color:var(--link);text-decoration:underline;text-underline-offset:3px}}a:focus-visible{{outline:2px solid var(--accent);outline-offset:4px}}.findings{{min-width:650px;table-layout:fixed}}.findings th:first-child{{width:18%}}.findings th:nth-child(2){{width:20%}}.findings th:nth-child(3){{width:27%}}.findings td{{overflow-wrap:anywhere}}.value{{white-space:pre-wrap;font-size:12px}}.location{{font-size:12px}}.notes{{border-top:1px solid var(--line);padding-top:22px;color:var(--muted);font-size:13px}}.downloads{{display:flex;flex-wrap:wrap;gap:12px 22px;margin-top:24px;font-size:13px}}footer{{margin-top:28px;font-size:12px;color:var(--muted)}}.sr-only{{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}}
@media(max-width:700px){{main{{padding:28px 16px}}h1{{font-size:28px}}.metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}.distribution-grid{{grid-template-columns:1fr}}.stats{{gap:26px}}.metric{{padding:14px}}.metric dd{{font-size:25px}}}}
@media print{{body{{background:#fff;font-size:10pt}}main{{max-width:none;padding:0}}h1{{font-size:24pt}}.metrics{{grid-template-columns:repeat(5,1fr);gap:7px}}.metric{{padding:10px}}.metric dd{{font-size:20pt}}.table-wrap{{overflow:visible;border-radius:0}}.attributes,.findings{{min-width:0;font-size:8pt}}th,td{{padding:7px}}.distribution-panel{{padding:12px}}.distribution-grid{{gap:10px}}.downloads{{display:none}}thead{{display:table-header-group}}tr{{break-inside:avoid}}.bar-track,.bar-fill,.status{{print-color-adjust:exact;-webkit-print-color-adjust:exact}}a{{color:inherit}}}}
.report-view{{border:0;padding:0;margin:22px 0 0}}.report-view legend{{font-size:13px;font-weight:600;margin-bottom:6px;padding:0}}
.report-view label{{display:inline-block;cursor:pointer;padding:7px 18px;border:1px solid var(--muted);border-radius:6px;background:var(--surface);color:var(--ink);font-weight:500}}
.report-view input:checked + label{{background:var(--ink);color:var(--surface);font-weight:650}}
.report-view input:focus-visible + label{{outline:3px solid var(--link);outline-offset:3px}}
#simple-report p + p,#simple-report ul + p{{margin-top:10px}}
.report-view,#simple-report{{display:none}}
@supports selector(:has(input:checked)){{
  .report-view,#simple-report{{display:block}}
  .report-view:has(#view-simple:checked) ~ #detailed-report,
  .report-view:has(#view-detailed:checked) ~ #simple-report{{display:none}}
}}
@media (forced-colors:active){{.report-view input:checked + label{{outline:2px solid Highlight;outline-offset:-4px}}}}
@media print{{.report-view{{display:none}}}}
</style></head><body><main>
<header><p class="eyebrow">Data Quality Inspector</p><h1>Inspection summary</h1>
<p class="source"><code>{_escape(source)}</code> <span class="subtle">· {_escape(_text(report.get('input_format', 'unknown')).upper())}</span></p>
<div class="status-row"><span class="status {status_class}">{_escape(status_label)}</span><p>{_escape(status_message)}</p></div></header>
<fieldset class="report-view"><legend>Report view</legend>
<input class="sr-only" type="radio" id="view-simple" name="report-view" aria-controls="simple-report" checked><label for="view-simple">Simple</label>
<input class="sr-only" type="radio" id="view-detailed" name="report-view" aria-controls="detailed-report"><label for="view-detailed">Detailed</label>
</fieldset>
<dl class="metrics">{metrics_html}</dl>
<div id="simple-report">{simple_html}</div>
<div id="detailed-report">
{audit_html}
<section class="scope"><h2>Checks run</h2><ul>{common_html}</ul></section>
{attribute_html}{distribution_html}{findings_html}{skipped_html}
<section class="notes"><h2>How to interpret this report</h2><ul>{notes_html}</ul></section>
</div>
<nav class="downloads" aria-label="Complete report files"><a href="report.json">Complete JSON report</a><a href="findings.csv">Findings CSV</a><a href="skipped-checks.csv">Skipped checks CSV</a>{review_download}</nav>
<footer>Generated by Data Quality Inspector · Source data is unchanged.</footer>
</main></body></html>
"""


def render_text(report: dict, source_name: str = "") -> str:
    """Render a plain-text companion that remains useful in a terminal or editor."""
    status_label, status_message = STATUSES.get(report.get("status"), ("Unknown status", "See the JSON report for details."))
    summaries = report.get("rating_summaries", [])
    lines = [
        "DATA QUALITY INSPECTOR — INSPECTION SUMMARY",
        f"Source: {_source_name(source_name)}",
        f"Format: {_text(report.get('input_format', 'unknown')).upper()}",
        f"{'Data validation' if 'audit' in report else 'Status'}: {status_label}. {status_message}",
        "",
        f"Records inspected: {_count(report.get('total_records')):,}",
    ]
    if summaries:
        lines.append(f"Rating values examined: {sum(_count(s.get('counts', {}).get('total')) for s in summaries):,}")
    lines.extend([
        f"Findings: {_count(report.get('finding_count')):,}",
        f"Affected records: {_count(report.get('affected_records')):,}",
        f"Skipped checks: {_count(report.get('skipped_check_count')):,}",
        "", "CHECKS RUN",
    ])
    checks = _common_checks(report)
    lines.extend(checks or ["No shared required-field or uniqueness checks were selected."])
    for summary in summaries:
        field = _text(summary.get("field", "Rating"))
        counts, stats = summary.get("counts", {}), summary.get("statistics", {})
        lines.extend([
            "", field.upper(),
            "Checks: " + ", ".join(_rule_labels(summary.get("rules", {}))) + ".",
            f"Valid / total: {_count(counts.get('valid')):,} / {_count(counts.get('total')):,}",
            f"Missing: {_count(counts.get('missing')):,}; nonnumeric: {_count(counts.get('invalid_numeric')):,}; noninteger: {_count(counts.get('non_integer')):,}; outside range: {_count(counts.get('out_of_range')):,}",
            f"Findings: {_count(counts.get('findings')):,}; skipped checks: {_count(counts.get('skipped_checks')):,}",
            f"Valid-score statistics: min {_number(stats.get('minimum'))}; max {_number(stats.get('maximum'))}; mean {_number(stats.get('mean'), mean=True)}",
        ])
        if stats.get("mean_note"):
            lines.append(_text(stats["mean_note"]))
        denominator = _count(stats.get("count"))
        if denominator:
            lines.append(f"Score distribution ({denominator:,} valid scores):")
            for bin_ in summary.get("distribution", []):
                count = _count(bin_.get("count"))
                lines.append(f"  {_number(bin_.get('value'))}: {count:,} ({count / denominator * 100:.1f}%)")
        else:
            lines.append("No valid numeric scores are available for this attribute.")
    if not summaries:
        config = report.get("config", {})
        if config.get("numeric_rating") or config.get("rating_range") is not None or "rating" in config.get("required", []):
            field = config.get("columns", {}).get("rating", "rating")
            rules = {"required": "rating" in config.get("required", []), "numeric": config.get("numeric_rating"), "range": config.get("rating_range")}
            lines.append(f"{_text(field)}: {', '.join(_rule_labels(rules))}.")
        lines.append("Attribute statistics are not available in this report.")
    for key, title, total_key, filename in (
        ("findings", "FINDINGS", "finding_count", "findings.csv"),
        ("skipped_checks", "CHECKS REQUIRING ATTENTION", "skipped_check_count", "skipped-checks.csv"),
    ):
        rows = report.get(key, [])
        if not rows:
            continue
        lines.extend(["", title, f"Showing {min(len(rows), PREVIEW_LIMIT):,} of {_count(report.get(total_key)):,}. Complete list: {filename}."])
        truncated = False
        for finding in rows[:PREVIEW_LIMIT]:
            value, clipped = _preview(finding)
            truncated |= clipped
            # Keep one preview item on one line even when the original string is multiline.
            value = value.replace("\r", "\\r").replace("\n", "\\n")
            location = f"Record {_text(finding.get('record', '—'))}"
            if finding.get("source_line") is not None:
                location += f", line {_text(finding['source_line'])}"
            lines.append(f"- {location}; {_text(finding.get('field', ''))}; {_text(finding.get('rule', ''))}; value {value}: {_text(finding.get('reason', ''))}")
        if truncated:
            lines.append(f"Long values are shortened to {VALUE_LIMIT} characters in this preview.")
    if "audit" in report:
        lines.extend(_audit_text(report["audit"]))
    details = "report.json, findings.csv, skipped-checks.csv" + (", review-queue.csv" if "audit" in report else "")
    lines.extend(["", "INTERPRETATION", *_interpretation(report), "", f"Full details: {details}."])
    return "\n".join(lines) + "\n"
