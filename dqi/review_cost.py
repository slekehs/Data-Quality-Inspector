"""Local estimates for blind, one-request-per-record text reviews."""

from decimal import Decimal

from .core import ConfigError, _number
from .json_utils import dumps_json
from .semantic import _schema, review_input, review_skip_reason
from .semantic_provider import MAX_OUTPUT_TOKENS, valid_model_id


# Standard, short-context text prices per million tokens from each model page.
RATES_DATE = "2026-11-23"
MODEL_RATES = {
    "gpt-6.2-sol": {"input_per_million": Decimal("2"), "output_per_million": Decimal("10")},
    "gpt-6.2-astra": {"input_per_million": Decimal("8"), "output_per_million": Decimal("40")},
}
LONG_CONTEXT_THRESHOLD = 272_000
BYTES_PER_TOKEN = 3
REQUEST_FRAMING_TOKENS = 32


def _custom_rates(rates):
    keys = ("input_per_million", "output_per_million")
    if not isinstance(rates, dict) or set(rates) != set(keys):
        raise ConfigError("Enter this model’s input and output prices in USD per million tokens to get an estimate.")
    result = {}
    for key in keys:
        price = _number(rates[key])
        if price is None or price <= 0:
            raise ConfigError("Each model price must be a positive, finite USD amount per million tokens.")
        if abs(price.adjusted()) > 18 or len(price.as_tuple().digits) > 18:
            raise ConfigError("Model prices support up to 18 significant digits and exponents between −18 and 18.")
        result[key] = price
    return result


def _estimated_tokens(byte_count):
    return (byte_count + BYTES_PER_TOKEN - 1) // BYTES_PER_TOKEN


def estimate_review_cost(plan, model, rates=None):
    """Price the selected, eligible requests locally using their frozen rubric and prompt."""
    if not valid_model_id(model):
        raise ConfigError("Enter a valid review model ID before getting an estimate.")
    standard = model in MODEL_RATES
    if standard and rates is not None:
        raise ConfigError("This model has published Standard pricing. Remove the custom rates and preview again.")
    prices = MODEL_RATES[model] if standard else _custom_rates(rates)
    attributes = plan["attributes"]
    instructions = plan["reviewer_instructions"]
    schema = _schema([attribute["field"] for attribute in attributes])
    # The provider sends the blind input as a JSON string. Its rubric repeats
    # verbatim in every request; serialize the invariant part just once.
    fixed_bytes = len(instructions.encode("utf-8")) + len(dumps_json(schema, indent=None))
    rubric_bytes = len(dumps_json(attributes, indent=None)) - len("[]")
    output_per_record = min(MAX_OUTPUT_TOKENS, 500 + 300 * len(attributes))
    eligible = input_tokens = 0
    long_requests = long_input_tokens = 0
    priced_write_tokens = 0
    for row in plan["records"]:
        if review_skip_reason(row):
            continue
        message_bytes = rubric_bytes + len(dumps_json(review_input(row, []), indent=None))
        tokens = _estimated_tokens(fixed_bytes + message_bytes) + REQUEST_FRAMING_TOKENS
        input_tokens += tokens
        eligible += 1
        long_context = standard and tokens > LONG_CONTEXT_THRESHOLD
        if long_context:
            long_requests += 1
            long_input_tokens += tokens
        # The Responses API places its implicit breakpoint at the end of the
        # user message. GPT-6.2's top-level instructions follow that breakpoint.
        visible_tokens = _estimated_tokens(message_bytes)
        if standard and visible_tokens >= 1024:
            priced_write_tokens += visible_tokens * (2 if long_context else 1)

    # The long-context tiers for Sol and Astra apply per request: 2x for
    # input, 1.5x for output. User-supplied rates apply uniformly.
    input_cost = (input_tokens + long_input_tokens) * prices["input_per_million"] / 1_000_000
    # The 30-minute write rate is 1.25x total; ordinary input is already counted.
    write_allowance = priced_write_tokens * prices["input_per_million"] * Decimal("0.25") / 1_000_000
    input_cost += write_allowance
    output_cost_per_token = (eligible + Decimal("0.5") * long_requests) * prices["output_per_million"] / 1_000_000
    assumptions = (
        f"Input allows about one token per {BYTES_PER_TOKEN} UTF-8 bytes plus {REQUEST_FRAMING_TOKENS} framing tokens per request. "
        f"Output assumes {output_per_record:,} tokens per eligible answer for reasoning and written scores. "
        f"The output-limit scenario uses {MAX_OUTPUT_TOKENS:,} output tokens per answer with the same estimated input. "
        "Actual billing varies with tokenization, output length, caching and your project’s rates."
    )
    if long_requests:
        assumptions += f" Long-context rates apply to {long_requests:,} estimated requests over 272,000 input tokens."
    if priced_write_tokens:
        assumptions += " Input pricing allows for a 30-minute cache write on each cacheable user message."
    return {
        "model": model, "currency": "USD", **prices,
        "rate_source": "OpenAI Standard" if standard else "User supplied",
        "pricing_url": f"https://developers.openai.com/api/docs/models/{model}" if standard else None,
        "rates_date": RATES_DATE if standard else None, "eligible_count": eligible,
        "estimated_input_tokens": input_tokens, "estimated_output_tokens": output_per_record * eligible,
        "max_output_tokens": MAX_OUTPUT_TOKENS * eligible,
        "estimated_usd": input_cost + output_cost_per_token * output_per_record,
        "cache_write_allowance_usd": write_allowance,
        "output_limit_usd": input_cost + output_cost_per_token * MAX_OUTPUT_TOKENS,
        "assumptions": assumptions,
    }
