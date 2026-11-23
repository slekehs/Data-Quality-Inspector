"""Offline, evidence-based review observations kept separate from rule failures."""

from collections import Counter, defaultdict
from dataclasses import replace
from decimal import Decimal

from .core import ConfigError, DatasetError, _missing, _number


OPTION_KEYS = {"prompt_field", "response_field", "duplicates", "conflicts", "review_thresholds", "coverage", "rule_origin"}
ORIGINS = {"helpsteer2", "standard", "custom"}
RELATED_RECORD_PREVIEW = 20


def _field(value, label, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a nonempty field name" + (" or null." if nullable else "."))
    return value


def _options(raw, result):
    if not isinstance(raw, dict) or set(raw) - OPTION_KEYS:
        raise ConfigError("Audit options must be an object containing only supported audit settings.")
    for name in ("duplicates", "conflicts"):
        if name in raw and not isinstance(raw[name], bool):
            raise ConfigError(f"Audit {name} must be true or false.")
    origin = raw.get("rule_origin", "custom")
    if not isinstance(origin, str) or origin not in ORIGINS:
        raise ConfigError("Audit rule_origin must be helpsteer2, standard, or custom.")
    thresholds = raw.get("review_thresholds", {})
    active_ratings = {rule.field for rule in result.config.rating_rules}
    if not isinstance(thresholds, dict):
        raise ConfigError("Audit review_thresholds must map active rating fields to numeric upper bounds.")
    parsed_thresholds = {}
    for field, bound in thresholds.items():
        if not isinstance(field, str) or field not in active_ratings:
            raise ConfigError(f"Review threshold field {field!r} is not an active rating attribute.")
        if isinstance(bound, bool) or not isinstance(bound, (int, float, Decimal)) or _number(bound) is None:
            raise ConfigError(f"Review threshold for {field!r} must be a finite number.")
        parsed_thresholds[field] = _number(bound)
    coverage = raw.get("coverage")
    if coverage is not None:
        if not isinstance(coverage, dict) or set(coverage) - {"field", "minimum", "expected_values"}:
            raise ConfigError("Audit coverage accepts only field, minimum, and expected_values.")
        minimum = coverage.get("minimum", 1)
        expected = coverage.get("expected_values", [])
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
            raise ConfigError("Coverage minimum must be an integer of at least 1.")
        if not isinstance(expected, list) or any(not isinstance(value, str) or not value.strip() for value in expected):
            raise ConfigError("Coverage expected_values must be a list of nonempty string labels.")
        if len(set(expected)) != len(expected):
            raise ConfigError("Coverage expected_values must not repeat a label.")
        coverage = {"field": _field(coverage.get("field"), "Coverage field"), "minimum": minimum, "expected_values": list(expected)}
    return {
        "prompt_field": _field(raw.get("prompt_field"), "Audit prompt_field", nullable=True),
        "response_field": _field(raw.get("response_field", result.config.columns["response"]), "Audit response_field"),
        "duplicates": raw.get("duplicates", False),
        "conflicts": raw.get("conflicts", False),
        "review_thresholds": parsed_thresholds,
        "coverage": coverage,
        "rule_origin": origin,
    }


def _valid_number(record, rule):
    raw = record.values.get(rule.field)
    number = _number(raw)
    if _missing(raw) or number is None:
        return None
    if rule.integer and number != number.to_integral_value():
        return None
    if rule.range is not None and not rule.range[0] <= number <= rule.range[1]:
        return None
    return number


def _context(record, config, options, rating_rules):
    context = {
        "record": record.number,
        "source_line": record.source_line,
        "prompt": record.values.get(options["prompt_field"]) if options["prompt_field"] else None,
        "response": record.values.get(options["response_field"]),
        "ratings": {rule.field: record.values[rule.field] for rule in rating_rules if rule.field in record.values},
    }
    id_column = config.columns["evaluation_id"]
    if id_column in record.values:
        context["evaluation_id"] = record.values[id_column]
    return context


def _coverage(dataset, options):
    if options is None:
        return None
    counts = Counter()
    missing = unsupported = 0
    for record in dataset.records:
        value = record.values.get(options["field"])
        if _missing(value):
            missing += 1
        elif not isinstance(value, str):
            unsupported += 1
        else:
            counts[value] += 1
    expected = set(options["expected_values"])
    minimum = options["minimum"]
    groups = [{"value": value, "count": count, "expected": value in expected, "below_minimum": count < minimum} for value, count in sorted(counts.items())]
    absent = [value for value in options["expected_values"] if value not in counts]
    gaps = [{"value": group["value"], "count": group["count"], "minimum": minimum, "kind": "below_minimum"} for group in groups if group["below_minimum"]]
    gaps.extend({"value": value, "count": 0, "minimum": minimum, "kind": "missing_expected"} for value in absent)
    return {
        **options,
        "eligible_records": sum(counts.values()),
        "missing_records": missing,
        "unsupported_records": unsupported,
        "groups": groups,
        "missing_expected_values": absent,
        "gaps": gaps,
    }


def add_audit(dataset, result, options):
    """Attach review evidence without changing structural findings or pass/fail status."""
    options = _options(options, result)
    if result.total_records != len(dataset.records):
        raise DatasetError("Audit input and inspection result refer to different record counts.")
    mapped = [options["response_field"]]
    if options["prompt_field"] is not None:
        mapped.append(options["prompt_field"])
    if options["coverage"] is not None:
        mapped.append(options["coverage"]["field"])
    if dataset.records:
        for field in mapped:
            if field not in dataset.headers:
                raise DatasetError(f"Audit field {field!r} is missing. Update its mapping or disable the audit option.")

    records = {record.number: record for record in dataset.records}
    rating_rules = result.config.rating_rules
    rules = {rule.field: rule for rule in rating_rules}
    valid_scores = {record.number: {rule.field: _valid_number(record, rule) for rule in rating_rules} for record in dataset.records}
    queue = {}

    def add_reason(number, kind, field, observed, rule, reason, action, related=(), **extra):
        if number not in records:
            raise DatasetError("A finding references a record outside the audited dataset.")
        if number not in queue:
            queue[number] = {**_context(records[number], result.config, options, rating_rules), "priority": "medium", "reasons": []}
        if kind == "data_error":
            queue[number]["priority"] = "high"
        queue[number]["reasons"].append({
            "kind": kind, "field": field, "observed": observed, "rule": rule,
            "reason": reason, "action": action,
            "related_records": list(related[:RELATED_RECORD_PREVIEW]),
            "related_record_count": len(related),
            "related_records_truncated": len(related) > RELATED_RECORD_PREVIEW,
            **extra,
        })

    for finding in result.findings:
        actions = {
            "required": "Recover the missing value from the original annotation or evaluation log. If it cannot be recovered, exclude the record with a documented reason rather than inventing a value.",
            "numeric": "Check the field mapping and source rubric. Recover the original numeric score, or change the rule if this field legitimately uses text categories.",
            "range": "Compare this value with the source rating scale and rubric version. Correct a transcription error or update the selected bounds if a different scale is intended.",
            "integer": "Check whether the source contains individual ratings or averaged scores. If fractions are legitimate aggregates, disable the whole-number rule instead of rounding the data.",
            "unique": "Check whether the ID identifies a response or a separate annotation. Correct accidental ID reuse or choose an appropriate unique identifier; keep intentional repeated annotations.",
        }
        add_reason(finding.record, "data_error", finding.field, finding.value, finding.rule, finding.reason,
                   actions.get(finding.rule, "Inspect the source value and correct it or adjust the rule if the value is legitimate."), finding.related_records, field_present=finding.field_present)
    for finding in result.skipped_checks:
        add_reason(finding.record, "skipped_check", finding.field, finding.value, finding.rule, finding.reason,
                   "Resolve the value or rule that prevented this check, then rerun the inspection.", finding.related_records, field_present=finding.field_present)

    pairs = defaultdict(list)
    if options["prompt_field"] is not None:
        for record in dataset.records:
            prompt = record.values.get(options["prompt_field"])
            response = record.values.get(options["response_field"])
            if isinstance(prompt, str) and prompt.strip() and isinstance(response, str) and response.strip():
                pairs[(prompt, response)].append(record.number)
    eligible = sum(len(group) for group in pairs.values())
    repeated_pairs = [numbers for numbers in pairs.values() if len(numbers) > 1]
    duplicate_groups = []
    if options["duplicates"] and options["prompt_field"] is not None:
        for numbers in repeated_pairs:
            group_id = f"duplicate-{len(duplicate_groups) + 1}"
            duplicate_groups.append({"group_id": group_id, "records": list(numbers), "record_count": len(numbers)})
            for number in numbers:
                add_reason(number, "duplicate", f"{options['prompt_field']} + {options['response_field']}", {"matching_record_count": len(numbers)}, "exact_prompt_response",
                           "The exact prompt and response also occur in other records. Repeated prompts with different responses are not flagged.",
                           "Confirm whether these copies are intended (for example, separate annotations) before removing or combining them.", numbers, group_id=group_id)

    conflict_groups = []
    if options["conflicts"] and options["prompt_field"] is not None and rating_rules:
        for numbers in repeated_pairs:
            group_id = f"conflict-{len(conflict_groups) + 1}"
            differences = []
            participating = set()
            for field in rules:
                buckets = defaultdict(list)
                for number in numbers:
                    value = valid_scores[number][field]
                    if value is not None:
                        buckets[value].append(number)
                if len(buckets) < 2:
                    continue
                values = [{"value": value, "records": members} for value, members in sorted(buckets.items())]
                differences.append({"field": field, "values": values})
                compared = sorted(number for members in buckets.values() for number in members)
                participating.update(compared)
                for number in compared:
                    add_reason(number, "conflicting_rating", field, records[number].values[field], "same_pair_different_ratings",
                               "Identical prompt and response text has different valid source ratings for this attribute. This may reflect legitimate annotator disagreement.",
                               "Compare the matching records, rubric version, and annotation provenance before deciding whether any rating needs correction.", compared, group_id=group_id)
            if differences:
                conflict_groups.append({"group_id": group_id, "records": sorted(participating), "record_count": len(participating), "pair_records": list(numbers), "differences": differences})

    matched_values = eligible_values = 0
    examples = []
    for field, bound in options["review_thresholds"].items():
        candidates = []
        for record in dataset.records:
            value = valid_scores[record.number][field]
            if value is None:
                continue
            eligible_values += 1
            if value <= bound:
                matched_values += 1
                add_reason(record.number, "low_rating", field, record.values[field], "source_score_at_or_below_threshold",
                           f"Existing source score {value} is at or below the selected review threshold {bound}. This is a review-priority flag, not a new quality judgment.",
                           "Read the prompt and response alongside the source rubric and rating. Keep or revise the annotation only after review.", threshold=bound)
            else:
                candidates.append((value, record))
        if candidates:
            best_value, best_record = max(candidates, key=lambda pair: (pair[0], -pair[1].number))
            examples.append({**_context(best_record, result.config, options, rating_rules), "field": field, "value": best_value,
                             "selection_reason": "Highest valid source score above the selected review threshold for this attribute; ties use the first record. This is existing rating evidence, not an independent assessment."})

    coverage = _coverage(dataset, options["coverage"])
    check_status = []
    for selected, check in ((options["duplicates"], "exact_duplicates"), (options["conflicts"], "rating_conflicts")):
        if not selected:
            state, reason = "not_selected", "This audit check was not selected."
        elif options["prompt_field"] is None:
            state, reason = "not_assessed", "A prompt field was not mapped; exact prompt-and-response comparisons could not run."
        elif check == "rating_conflicts" and not rating_rules:
            state, reason = "not_assessed", "No active rating attributes were selected for comparison."
        else:
            state, reason = "completed", "Compared exact, nonblank prompt and response strings without trimming, case folding, or paraphrase matching."
            if check == "rating_conflicts":
                reason += " Only values satisfying the selected rating rules participated in score comparisons."
        skipped_records = len(dataset.records) - eligible if state == "completed" else len(dataset.records) if state == "not_assessed" else 0
        item = {"check": check, "status": state, "eligible_records": eligible if state == "completed" else 0, "skipped_records": skipped_records, "reason": reason}
        if check == "rating_conflicts" and state == "completed":
            pair_records = [number for group in pairs.values() for number in group]
            compared_values = sum(valid_scores[number][field] is not None for number in pair_records for field in rules)
            item.update(eligible_values=compared_values, skipped_values=len(pair_records) * len(rules) - compared_values)
        check_status.append(item)
    check_status.append({
        "check": "review_thresholds", "status": "completed" if options["review_thresholds"] else "not_selected",
        "eligible_values": eligible_values,
        "skipped_values": len(dataset.records) * len(options["review_thresholds"]) - eligible_values,
        "matched_values": matched_values,
        "reason": "Compared valid source scores with explicitly selected inclusive upper bounds; no attributes receive implicit review thresholds." if options["review_thresholds"] else "No source-score review thresholds were selected.",
    })
    check_status.append({
        "check": "category_coverage", "status": "completed" if coverage is not None else "not_selected",
        "eligible_records": coverage["eligible_records"] if coverage else 0,
        "skipped_records": coverage["missing_records"] + coverage["unsupported_records"] if coverage else 0,
        "reason": "Counted exact string labels against the selected minimum and expected labels. These counts do not establish statistical representativeness." if coverage else "No categorical coverage field was selected.",
    })
    check_status.extend([
        {"check": "annotator_agreement", "status": "not_assessed", "reason": "No per-annotator identity and rating schema was configured; repeated-record conflicts are not an inter-annotator agreement statistic."},
        {"check": "split_leakage", "status": "not_assessed", "reason": "Only one input file was supplied; overlap between training and evaluation splits was not assessed."},
    ])
    queue_values = sorted(queue.values(), key=lambda item: (item["priority"] != "high", item["record"]))
    duplicate_records = {number for group in duplicate_groups for number in group["records"]}
    conflict_records = {number for group in conflict_groups for number in group["records"]}
    audit = {
        "options": options,
        "provenance": {
            "rule_origin": options["rule_origin"],
            "structural_rules": "The report's config is the exact structural-rule snapshot. rule_origin is the selected UI profile label; it does not authenticate the input dataset or establish that its original rubric was verified.",
            "review_thresholds": "These are explicit user-selected or tool-default review-priority heuristics recorded in audit.options.review_thresholds, not official dataset thresholds or a dataset-wide pass/fail rubric.",
            "rating_evidence": "All displayed scores come from the input file. No model or human regraded the responses during this audit.",
        },
        "check_status": check_status,
        "limitations": [
            "Exact duplicate and score-conflict observations require contextual review; repeated annotations can be legitimate.",
            "Prompt/response correctness, instruction following, and rubric alignment were not semantically evaluated.",
            "Coverage compares observed labels with the configured expectations; it does not prove that the dataset represents a target population.",
            "Source-score examples show existing labels only. Higher complexity or verbosity is not inherently better.",
            "Related-record lists inside review reasons preview at most 20 records. Full duplicate and conflict group membership is retained in the group details.",
        ],
        "duplicate_groups": duplicate_groups,
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_record_count": len(duplicate_records),
        "conflict_groups": conflict_groups,
        "conflict_group_count": len(conflict_groups),
        "conflict_record_count": len(conflict_records),
        "coverage": coverage,
        "review_queue": queue_values,
        "review_record_count": len(queue_values),
        "review_reason_count": sum(len(item["reasons"]) for item in queue_values),
        "threshold_match_count": matched_values,
        "positively_rated_examples": examples,
    }
    return replace(result, audit=audit)
