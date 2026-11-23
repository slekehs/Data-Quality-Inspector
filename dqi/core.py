"""Format-independent deterministic validation. No third-party packages."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from decimal import Decimal, DecimalException, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path


class DatasetError(ValueError):
    """The source cannot be inspected without correcting its structure or mapping."""


class ConfigError(ValueError):
    """The requested rules are invalid or ambiguous."""


FIELDS = ("evaluation_id", "response", "rating", "explanation")
NUMBER_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _number(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    value = str(value).strip()
    if not NUMBER_PATTERN.fullmatch(value):
        return None
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _id_key(value: object) -> tuple:
    # JSON numeric 1 and 1.0 represent the same ID; strings and booleans are distinct.
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float, Decimal)):
        return ("number", _number(value))
    return ("string", value)


def _parse_range(bounds, label):
    if bounds is None:
        return None
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise ConfigError(f"{label} must be [minimum, maximum] or null.")
    if any(isinstance(value, bool) or not isinstance(value, (int, float, Decimal)) for value in bounds):
        raise ConfigError(f"{label} bounds must be finite numbers.")
    parsed = tuple(_number(value) for value in bounds)
    if None in parsed or parsed[0] > parsed[1]:
        raise ConfigError(f"{label} bounds must be finite and minimum must not exceed maximum.")
    return parsed


@dataclass(frozen=True)
class RatingRule:
    field: str
    required: bool = True
    numeric: bool = True
    integer: bool = False
    range: tuple[Decimal, Decimal] | None = (Decimal("1"), Decimal("5"))

    @classmethod
    def from_dict(cls, name, raw):
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("Rating field names must be nonempty strings.")
        if not isinstance(raw, dict) or set(raw) - {"required", "numeric", "integer", "range"}:
            raise ConfigError(f"Invalid rules for rating field {name!r}; use required, numeric, integer, and range.")
        for key in ("required", "numeric", "integer"):
            if key in raw and not isinstance(raw[key], bool):
                raise ConfigError(f"Rating rule {name!r}.{key} must be true or false.")
        return cls(name, raw.get("required", True), raw.get("numeric", True), raw.get("integer", False), _parse_range(raw.get("range", [1, 5]), f"{name}.range"))

    @property
    def enabled(self):
        return self.required or self.numeric or self.integer or self.range is not None

    def to_dict(self):
        return {"required": self.required, "numeric": self.numeric, "integer": self.integer, "range": list(self.range) if self.range is not None else None}


@dataclass(frozen=True)
class Config:
    columns: dict[str, str] = field(default_factory=lambda: {key: key for key in FIELDS})
    required: tuple[str, ...] = ("evaluation_id", "response", "rating")
    unique_id: bool = True
    numeric_rating: bool = True
    rating_range: tuple[Decimal, Decimal] | None = (Decimal("1"), Decimal("5"))
    ratings: dict[str, RatingRule] | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> Config:
        if not isinstance(raw, dict):
            raise ConfigError("Configuration must be a JSON object.")
        allowed = {"columns", "required", "unique_id", "numeric_rating", "rating_range", "ratings"}
        unknown = set(raw) - allowed
        if unknown:
            raise ConfigError(f"Unknown configuration fields: {', '.join(sorted(unknown))}.")
        columns = {key: key for key in FIELDS}
        overrides = raw.get("columns", {})
        if not isinstance(overrides, dict) or set(overrides) - set(FIELDS):
            raise ConfigError("Column mappings must use evaluation_id, response, rating, or explanation.")
        if any(not isinstance(value, str) or not value.strip() for value in overrides.values()):
            raise ConfigError("Mapped column names must be nonempty strings.")
        columns.update(overrides)
        multi = "ratings" in raw
        if multi and any(key in raw for key in ("numeric_rating", "rating_range")):
            raise ConfigError("Use ratings or legacy numeric_rating/rating_range options, not both.")
        required = raw.get("required", [key for key in cls().required if not multi or key != "rating"])
        if not isinstance(required, list) or any(not isinstance(key, str) or key not in FIELDS for key in required):
            raise ConfigError("Required fields must be a list of supported semantic field names.")
        if len(set(required)) != len(required):
            raise ConfigError("Required fields must not contain duplicates.")
        if multi and "rating" in required:
            raise ConfigError("In ratings mode, set required on each rating rule instead of using legacy 'rating'.")
        for key in ("unique_id", "numeric_rating"):
            if key in raw and not isinstance(raw[key], bool):
                raise ConfigError(f"{key} must be true or false.")
        ratings = None
        if multi:
            if not isinstance(raw["ratings"], dict):
                raise ConfigError("ratings must be an object mapping source field names to rules.")
            ratings = {name: RatingRule.from_dict(name, rule) for name, rule in raw["ratings"].items()}
        rating_range = _parse_range(raw.get("rating_range", [1, 5]), "rating_range")
        config = cls(columns, tuple(required), raw.get("unique_id", True), raw.get("numeric_rating", True), rating_range, ratings)
        mapped = [config.columns[key] for key in config.active_fields if key != "rating"] + [rule.field for rule in config.rating_rules]
        if len(set(mapped)) != len(mapped):
            raise ConfigError("Different active fields must map to different source columns.")
        return config

    @property
    def active_fields(self) -> tuple[str, ...]:
        active = set(self.required)
        if self.unique_id:
            active.add("evaluation_id")
        if self.ratings is None and (self.numeric_rating or self.rating_range is not None):
            active.add("rating")
        return tuple(key for key in FIELDS if key in active)

    @property
    def rating_rules(self) -> tuple[RatingRule, ...]:
        if self.ratings is not None:
            return tuple(rule for rule in self.ratings.values() if rule.enabled)
        rule = RatingRule(self.columns["rating"], "rating" in self.required, self.numeric_rating, False, self.rating_range)
        return (rule,) if rule.enabled else ()

    @property
    def has_checks(self):
        return bool(self.active_fields or self.rating_rules)

    def to_dict(self) -> dict:
        result = {
            "columns": dict(self.columns),
            "required": list(self.required),
            "unique_id": self.unique_id,
        }
        if self.ratings is None:
            result.update(numeric_rating=self.numeric_rating, rating_range=list(self.rating_range) if self.rating_range is not None else None)
        else:
            result["ratings"] = {name: rule.to_dict() for name, rule in self.ratings.items()}
        return result


@dataclass(frozen=True)
class Record:
    number: int
    source_line: int | None
    values: dict[str, object]


@dataclass(frozen=True)
class Dataset:
    headers: tuple[str, ...]
    records: tuple[Record, ...]
    input_format: str = "csv"


def load_csv(path: str | Path) -> Dataset:
    """Compatibility entry point; explicitly read CSV, including .csv.gz."""
    return load_dataset(path, format="csv")


def load_dataset(path: str | Path, format: str | None = None) -> Dataset:
    from .readers import load_dataset as read
    return read(path, format=format)


@dataclass(frozen=True)
class Finding:
    record: int
    source_line: int | None
    field: str
    value: object
    rule: str
    reason: str
    related_records: tuple[int, ...] = ()
    field_present: bool = True

    @property
    def value_type(self) -> str:
        if not self.field_present:
            return "missing"
        if self.value is None:
            return "null"
        if isinstance(self.value, bool):
            return "boolean"
        if isinstance(self.value, str):
            return "string"
        if isinstance(self.value, (int, float, Decimal)):
            return "number"
        return "object" if isinstance(self.value, dict) else "array"

    def to_dict(self) -> dict:
        return {**asdict(self), "value_type": self.value_type}


@dataclass(frozen=True)
class InspectionResult:
    total_records: int
    config: Config
    findings: tuple[Finding, ...]
    skipped_checks: tuple[Finding, ...]
    input_format: str = "csv"
    rating_summaries: tuple[dict, ...] = ()
    audit: dict | None = None

    @property
    def affected_records(self) -> int:
        return len({finding.record for finding in self.findings})

    @property
    def status(self) -> str:
        if not self.config.has_checks:
            return "no_checks_selected"
        if self.total_records == 0:
            return "empty_dataset"
        if self.findings:
            return "issues_found"
        if self.skipped_checks:
            return "incomplete"
        return "passed"

    def to_dict(self) -> dict:
        result = {
            "status": self.status,
            "input_format": self.input_format,
            "total_records": self.total_records,
            "affected_records": self.affected_records,
            "finding_count": len(self.findings),
            "skipped_check_count": len(self.skipped_checks),
            "config": self.config.to_dict(),
            "rating_summaries": list(self.rating_summaries),
            "findings": [finding.to_dict() for finding in self.findings],
            "skipped_checks": [check.to_dict() for check in self.skipped_checks],
        }
        if self.audit is not None:
            result["audit"] = self.audit
        return result


def _statistics(values):
    result = {"count": len(values), "minimum": min(values) if values else None, "maximum": max(values) if values else None, "mean": None}
    if values:
        try:
            with localcontext() as context:
                nonzero = [value for value in values if value]
                if not nonzero:
                    result["mean"] = Decimal("0.000000")
                    return result
                integer_digits = max(value.adjusted() + 1 for value in nonzero)
                lowest_exponent = min(value.as_tuple().exponent for value in nonzero)
                precision = max(40, integer_digits - lowest_exponent + len(str(len(values))) + 8, integer_digits + 8)
                if precision > 10000:
                    raise InvalidOperation
                context.prec = precision
                result["mean"] = (sum(nonzero, Decimal(0)) / len(values)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        except DecimalException:
            result["mean_note"] = "Mean unavailable at supported reporting precision."
    return result


def _distribution(values, rule):
    counts = Counter(values)
    if rule.integer and rule.range is not None:
        lower, upper = rule.range
        # Avoid expanding unbounded or very large score domains into report rows.
        if lower >= -10000 and upper <= 10000 and upper - lower <= 20:
            start = int(lower.to_integral_value(rounding="ROUND_CEILING"))
            end = int(upper.to_integral_value(rounding="ROUND_FLOOR"))
            for value in range(start, end + 1):
                counts.setdefault(Decimal(value), 0)
    return [{"value": value, "count": count} for value, count in sorted(counts.items())]


def inspect(dataset: Dataset, config: Config | None = None) -> InspectionResult:
    config = config or Config()
    rating_rules = config.rating_rules
    common_fields = [config.columns[key] for key in config.active_fields if key != "rating"]
    active_columns = common_fields + [rule.field for rule in rating_rules]
    if dataset.records:
        for column in active_columns:
            if column not in dataset.headers:
                raise DatasetError(f"Mapped field {column!r} is missing. Update the mapping or disable its rules.")
        for row in dataset.records:
            for column in active_columns:
                if isinstance(row.values.get(column), (dict, list)):
                    raise DatasetError(f"Record {row.number}, field {column!r} contains an object or array. Map a scalar field; nested-field mapping is not supported.")
    groups: dict[tuple, list[int]] = defaultdict(list)
    if config.unique_id:
        for row in dataset.records:
            value = row.values.get(config.columns["evaluation_id"])
            if not _missing(value):
                groups[_id_key(value)].append(row.number)
    findings, skipped = [], []
    summaries = {rule.field: {"field": rule.field, "rules": rule.to_dict(), "counts": dict.fromkeys(("total", "missing", "invalid_numeric", "non_integer", "out_of_range", "valid", "findings", "skipped_checks"), 0)} for rule in rating_rules}
    valid_values = {rule.field: [] for rule in rating_rules}
    for row in dataset.records:
        def finding(column, rule, reason, related=()):
            return Finding(row.number, row.source_line, column, row.values.get(column), rule, reason, related, column in row.values)

        for semantic in config.required:
            if semantic == "rating":
                continue
            column = config.columns[semantic]
            if _missing(row.values.get(column)):
                findings.append(finding(column, "required", "A value is required."))
        for rule in rating_rules:
            column = rule.field
            counts = summaries[column]["counts"]
            counts["total"] += 1
            raw = row.values.get(column)
            before_findings, before_skipped = len(findings), len(skipped)
            if _missing(raw):
                counts["missing"] += 1
                if rule.required:
                    findings.append(finding(column, "required", "A value is required."))
            else:
                number = _number(raw)
                if number is None:
                    counts["invalid_numeric"] += 1
                    if rule.numeric:
                        findings.append(finding(column, "numeric", "Expected a finite number."))
                    for check, enabled in (("integer", rule.integer), ("range", rule.range is not None)):
                        if enabled:
                            skipped.append(finding(column, check, f"{check.capitalize()} was not evaluated because the value is not a finite number."))
                else:
                    valid = True
                    if rule.integer and number != number.to_integral_value():
                        counts["non_integer"] += 1
                        findings.append(finding(column, "integer", "Expected a whole-number rating."))
                        valid = False
                    if rule.range is not None and not rule.range[0] <= number <= rule.range[1]:
                        counts["out_of_range"] += 1
                        findings.append(finding(column, "range", f"Expected a value from {rule.range[0]} to {rule.range[1]}, inclusive."))
                        valid = False
                    if valid:
                        counts["valid"] += 1
                        valid_values[column].append(number)
            counts["findings"] += len(findings) - before_findings
            counts["skipped_checks"] += len(skipped) - before_skipped
        if config.unique_id:
            id_value = row.values.get(config.columns["evaluation_id"])
            group = groups.get(_id_key(id_value), []) if not _missing(id_value) else []
            if len(group) > 1:
                findings.append(finding(config.columns["evaluation_id"], "unique", "ID must be unique; matching data records: " + ", ".join(map(str, group)) + ".", tuple(group)))
    for rule in rating_rules:
        summaries[rule.field]["statistics"] = _statistics(valid_values[rule.field])
        summaries[rule.field]["distribution"] = _distribution(valid_values[rule.field], rule)
    return InspectionResult(len(dataset.records), config, tuple(findings), tuple(skipped), dataset.input_format, tuple(summaries.values()))
