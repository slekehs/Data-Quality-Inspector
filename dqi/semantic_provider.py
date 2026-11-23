"""Bounded, non-retrying OpenAI transport for optional answer review.

The caller supplies a key for this job; this module never reads or saves keys.
The review engine owns rubric and evidence validation after structured parsing.
"""

from __future__ import annotations

import re
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from .json_utils import dumps_json, loads_json


ENDPOINT = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-6.2-sol"
TIMEOUT_SECONDS = 60
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_TOKENS = 8000
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")


class ProviderError(Exception):
    """A fixed, safe-to-display message, never an upstream response or key."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("redirect", "OpenAI returned an unexpected redirect. No redirect was followed.")


def _http_error(error: HTTPError) -> ProviderError:
    """Inspect only a bounded error code to distinguish credits from rate limits."""
    status = error.code
    upstream_code = None
    try:
        if status == 429:
            data = error.read(65537)
            if len(data) <= 65536:
                payload = loads_json(data.decode("utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                    upstream_code = payload["error"].get("code")
    except Exception:
        pass
    finally:
        error.close()
    if status == 401:
        return ProviderError("authentication", "OpenAI did not accept this API key. Update your saved key in Settings or enter a key for this review.")
    if status == 403:
        return ProviderError("permission", "This API project cannot access the requested service or model. Check its permissions.")
    if status in {400, 404, 422}:
        return ProviderError("model_or_request", "OpenAI could not accept this model or review format. Choose a model that supports Responses API structured outputs.")
    if status == 429 and upstream_code in {"insufficient_quota", "credit_balance_exhausted", "billing_hard_limit_reached"}:
        return ProviderError("credits", "This API organization has no available credits or has reached its billing limit. Check API Billing before starting another review.")
    if status == 429:
        return ProviderError("rate_limit", "OpenAI's request limit was reached. The batch stopped; try a smaller batch later.")
    if status in {408, 504}:
        return ProviderError("timeout", "The OpenAI request timed out. It was not retried; check API usage before starting another review.")
    if 300 <= status < 400:
        return ProviderError("redirect", "OpenAI returned an unexpected redirect. No redirect was followed.")
    return ProviderError("unavailable", "OpenAI could not complete this request. It was not retried; check API usage before trying again.")


def _parse_envelope(raw: bytes):
    try:
        envelope = loads_json(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        raise ProviderError("invalid_response", "OpenAI returned an unreadable response. No answer-review result was accepted.") from None
    if not isinstance(envelope, dict):
        raise ProviderError("invalid_response", "OpenAI returned an invalid response. No answer-review result was accepted.")
    return envelope


def _response_metadata(envelope):
    """Read billed usage before review extraction, including refused/incomplete runs."""
    usage = {}
    if "usage" in envelope:
        stats = envelope["usage"]
        if not isinstance(stats, dict) or any(type(stats.get(key)) is not int or stats[key] < 0 for key in ("input_tokens", "output_tokens")):
            raise ProviderError("invalid_response", "OpenAI returned invalid usage information. The batch stopped to avoid reporting inaccurate usage.")
        usage = {key: stats[key] for key in ("input_tokens", "output_tokens")}
        if "total_tokens" in stats and (type(stats["total_tokens"]) is not int or stats["total_tokens"] != sum(usage.values())):
            raise ProviderError("invalid_response", "OpenAI returned inconsistent usage information. The batch stopped to avoid reporting inaccurate usage.")
    actual_model = envelope.get("model")
    if actual_model is not None and (not isinstance(actual_model, str) or not _MODEL.fullmatch(actual_model)):
        raise ProviderError("invalid_response", "OpenAI returned invalid model information. No review result was accepted.")
    return usage, actual_model


def _extract_review(envelope):
    if envelope.get("status") != "completed" or envelope.get("error") is not None:
        raise ProviderError("incomplete", "OpenAI did not complete this review. Partial model output was not accepted and the request was not retried.")
    output = envelope.get("output")
    if not isinstance(output, list):
        raise ProviderError("invalid_response", "OpenAI returned no usable review content.")
    text = []
    for item in output:
        if not isinstance(item, dict):
            raise ProviderError("invalid_response", "OpenAI returned an invalid review format.")
        if item.get("type") == "reasoning":
            continue
        if item.get("type") != "message" or item.get("role") != "assistant" or not isinstance(item.get("content"), list):
            raise ProviderError("invalid_response", "OpenAI returned an unexpected review format.")
        if item.get("status", "completed") != "completed":
            raise ProviderError("incomplete", "OpenAI did not finish the review message. Partial output was not accepted.")
        for part in item["content"]:
            if not isinstance(part, dict):
                raise ProviderError("invalid_response", "OpenAI returned an invalid review format.")
            if part.get("type") == "refusal":
                raise ProviderError("refusal", "The model declined this review. No suggested ratings were assigned.")
            if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                raise ProviderError("invalid_response", "OpenAI returned an unexpected review format.")
            text.append(part["text"])
    try:
        result = loads_json("".join(text))
    except (ValueError, RecursionError):
        raise ProviderError("invalid_response", "The model returned incomplete or invalid review JSON. No suggested ratings were accepted.") from None
    if not isinstance(result, dict) or not isinstance(result.get("assessments"), list):
        raise ProviderError("invalid_response", "The model returned an invalid review structure. No suggested ratings were accepted.")

    return result


def valid_api_key(key):
    return isinstance(key, str) and 16 <= len(key) <= 4096 and all(33 <= ord(char) <= 126 for char in key)


def valid_model_id(model):
    return isinstance(model, str) and _MODEL.fullmatch(model) is not None


class _Judge:
    def __init__(self, api_key, model):
        if not valid_api_key(api_key):
            raise ProviderError("api_key", "Enter a valid API key without spaces or control characters.")
        if not valid_model_id(model):
            raise ProviderError("model", "Enter a valid model ID using letters, numbers, periods, colons, underscores, or hyphens.")
        self._api_key = api_key
        self._model = model
        self.last_usage = {}
        self.last_model = None
        self.last_response_received = False
        # Explicitly disable environment proxy settings and cross-origin redirects.
        self._opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))

    def close(self):
        """Release this job's key reference. No credentials are written to disk."""
        self._api_key = None

    def __call__(self, request):
        self.last_usage = {}
        self.last_model = None
        self.last_response_received = False
        if self._api_key is None:
            raise ProviderError("api_key", "This review job has ended. Enter an API key to start a new review.")
        if not isinstance(request, dict) or set(request) != {"instructions", "input", "schema"} or not isinstance(request["instructions"], str) or not request["instructions"].strip() or not isinstance(request["input"], dict) or not isinstance(request["schema"], dict):
            raise ProviderError("request", "The inspector could not prepare a valid answer-review request.")
        try:
            payload = {
                "model": self._model,
                "instructions": request["instructions"],
                "input": [{"role": "user", "content": [{"type": "input_text", "text": dumps_json(request["input"], indent=None)}]}],
                "store": False,
                "service_tier": "default",
                "text": {"format": {"type": "json_schema", "name": "answer_review", "strict": True, "schema": request["schema"]}},
                "max_output_tokens": MAX_OUTPUT_TOKENS,
            }
            if self._model == DEFAULT_MODEL or self._model.startswith(DEFAULT_MODEL + "-"):
                payload["reasoning"] = {"effort": "medium"}
            body = dumps_json(payload, indent=None).encode("utf-8")
        except (ValueError, TypeError, RecursionError):
            raise ProviderError("request", "The inspector could not encode this answer-review request.") from None
        http_request = Request(ENDPOINT, data=body, method="POST", headers={"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self._opener.open(http_request, timeout=TIMEOUT_SECONDS) as response:
                if response.getcode() != 200 or response.geturl() != ENDPOINT:
                    raise ProviderError("invalid_response", "OpenAI returned an unexpected response. No review result was accepted.")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ProviderError("response_limit", "The model response exceeded the review size limit. No result was accepted.")
        except ProviderError:
            raise
        except HTTPError as exc:
            raise _http_error(exc) from None
        except (socket.timeout, TimeoutError):
            raise ProviderError("timeout", "The OpenAI request timed out. It was not retried; check API usage before starting another review.") from None
        except URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise ProviderError("timeout", "The OpenAI request timed out. It was not retried; check API usage before starting another review.") from None
            raise ProviderError("connection", "The inspector could not reach OpenAI securely. Check your network connection and try again.") from None
        except Exception:
            raise ProviderError("connection", "The inspector could not complete the connection to OpenAI. The request was not retried.") from None
        envelope = _parse_envelope(raw)
        usage, model = _response_metadata(envelope)
        self.last_usage = usage
        self.last_model = model
        self.last_response_received = model is not None
        return _extract_review(envelope)


def make_judge(*, api_key: str, model: str = DEFAULT_MODEL):
    """Return a callable with per-call response metadata and close().

    last_response_received means a parsed envelope reported a valid model ID;
    it does not imply a successful assessment. Missing usage stays unknown.
    """
    return _Judge(api_key, model)
