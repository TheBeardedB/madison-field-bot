import json
import logging
import os
import urllib.error
import urllib.request
from typing import Dict, List

logger = logging.getLogger(__name__)


def _preview(text: str, limit: int = 500) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _format_state_counts(parks: Dict[str, Dict[str, str]]) -> str:
    summary = []
    for park_name, field_map in parks.items():
        counts = {"open": 0, "closed": 0, "unknown": 0}
        for state in field_map.values():
            if state in counts:
                counts[state] += 1
        summary.append(
            f"{park_name}: open={counts['open']} closed={counts['closed']} unknown={counts['unknown']}"
        )
    return "; ".join(summary)


def _coerce_confidence(value) -> float:
    try:
        if isinstance(value, str):
            raw_value = value.strip().lower()
            labeled_values = {
                "high": 0.9,
                "very high": 0.98,
                "medium": 0.65,
                "moderate": 0.65,
                "low": 0.3,
                "very low": 0.1,
                "confident": 0.9,
                "uncertain": 0.35,
            }
            if raw_value in labeled_values:
                return labeled_values[raw_value]
            value = raw_value
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    if confidence != confidence:  # NaN check
        return 0.0
    if confidence < 0.0:
        return 0.0
    if confidence > 1.0:
        return 1.0
    return confidence


class GitHubModelsFieldParser:
    """Field-status extraction via GitHub Models."""

    def __init__(self):
        self.token = os.getenv("GITHUB_MODELS_TOKEN") or os.getenv("GITHUB_TOKEN")
        enabled_env = os.getenv("LLM_FIELD_STATUS_ENABLED")
        if enabled_env is None:
            enabled_env = os.getenv("LLM_PARTIAL_PARSE_ENABLED")
        if enabled_env is None:
            enabled_env = "true" if self.token else "false"
        self.enabled = enabled_env.lower() == "true"
        self.endpoint = os.getenv(
            "GITHUB_MODELS_ENDPOINT", "https://models.github.ai/inference"
        ).rstrip("/")
        self.model = os.getenv("LLM_MODEL", "openai/gpt-4.1-mini")
        self.timeout_seconds = int(os.getenv("LLM_PARTIAL_PARSE_TIMEOUT_SECONDS", "12"))
        self.min_confidence = float(os.getenv("LLM_PARTIAL_MIN_CONFIDENCE", "0.65"))
        self.verbosity = self._verbosity_depth()

    @staticmethod
    def _verbosity_depth() -> int:
        value = os.getenv("LOG_VERBOSITY", "vv").strip().lower()
        if value == "":
            return 0
        if value == "v":
            return 1
        if value == "vv":
            return 2
        if value == "vvv":
            return 3
        return 2

    def _should_log_detail(self) -> bool:
        return self.verbosity >= 2

    def _should_log_trace(self) -> bool:
        return self.verbosity >= 3

    def is_ready(self) -> bool:
        return self.enabled and bool(self.token)

    def extract_closed_fields_with_llm(
        self, content: str, canonical_fields: List[str]
    ) -> Dict:
        """
        Returns:
          {
            "closed_fields": [..canonical names..],
            "confidence": float,
            "raw_closed_fields": [..],
            "reason": "ok|disabled|missing_token|http_error|invalid_response|..."
          }
        """
        if not self.enabled:
            return {
                "closed_fields": [],
                "confidence": 0.0,
                "raw_closed_fields": [],
                "reason": "disabled",
            }
        if not self.token:
            return {
                "closed_fields": [],
                "confidence": 0.0,
                "raw_closed_fields": [],
                "reason": "missing_token",
            }

        system_prompt = (
            "You extract only closed field names from city field status updates. "
            "Rules: do not infer closures that are not explicit, do not include open fields, "
            "if all fields are closed return ['All Fields'], if no closed fields are present return []. "
            "Return strict JSON only with keys: closed_fields (array of strings), confidence (numeric 0..1). "
            "Do not use words like high, medium, or low for confidence. "
            "Use names from the allowed canonical list when possible."
        )
        user_prompt = json.dumps(
            {
                "content": content,
                "allowed_canonical_fields": canonical_fields,
                "instructions": "Extract only closed fields. Confidence must be a numeric value between 0 and 1.",
            }
        )

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

        logger.info(
            "LLM field-status request model=%s enabled=%s min_confidence=%.2f content_len=%s verbosity=%s",
            self.model,
            self.enabled,
            self.min_confidence,
            len(content or ""),
            self.verbosity,
        )
        if self._should_log_detail():
            logger.info("LLM field-status system prompt: %s", _preview(system_prompt, 1400))
            logger.info("LLM field-status user prompt: %s", _preview(user_prompt, 2200))

        req = urllib.request.Request(
            f"{self.endpoint}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw_resp = resp.read().decode("utf-8")
                if self._should_log_detail():
                    logger.info(
                        "LLM field-status HTTP %s bytes=%s",
                        getattr(resp, "status", "unknown"),
                        len(raw_resp),
                    )
        except urllib.error.HTTPError as e:
            logger.warning("LLM partial parse HTTP error: %s", e)
            return {
                "closed_fields": [],
                "confidence": 0.0,
                "raw_closed_fields": [],
                "reason": "http_error",
            }
        except urllib.error.URLError as e:
            logger.warning("LLM partial parse network error: %s", e)
            return {
                "closed_fields": [],
                "confidence": 0.0,
                "raw_closed_fields": [],
                "reason": "network_error",
            }
        except Exception as e:
            logger.warning("LLM partial parse error: %s", e)
            return {
                "closed_fields": [],
                "confidence": 0.0,
                "raw_closed_fields": [],
                "reason": "request_error",
            }

        try:
            payload = json.loads(raw_resp)
            content_text = payload["choices"][0]["message"]["content"]
            if self._should_log_trace():
                logger.info("LLM field-status response content preview: %s", _preview(content_text, 2500))
            parsed = json.loads(content_text)
            raw_closed = parsed.get("closed_fields", [])
            confidence = _coerce_confidence(parsed.get("confidence", 0.0))
            if not isinstance(raw_closed, list):
                raw_closed = []
        except Exception:
            if self._should_log_detail():
                logger.info("LLM field-status invalid JSON response preview: %s", _preview(raw_resp, 2000))
            return {
                "closed_fields": [],
                "confidence": 0.0,
                "raw_closed_fields": [],
                "reason": "invalid_response",
            }

        canonical_map = {name.lower(): name for name in canonical_fields}
        normalized = []
        for item in raw_closed:
            if not isinstance(item, str):
                continue
            key = " ".join(item.strip().split()).lower()
            if key in canonical_map:
                name = canonical_map[key]
                if name not in normalized:
                    normalized.append(name)

        if self._should_log_detail():
            logger.info(
                "LLM closed-field parsed confidence=%.2f reason=%s raw=%s normalized=%s",
                confidence,
                "ok" if confidence >= self.min_confidence else "low_confidence",
                raw_closed,
                normalized,
            )

        return {
            "closed_fields": normalized,
            "confidence": confidence,
            "raw_closed_fields": raw_closed,
            "reason": "ok" if confidence >= self.min_confidence else "low_confidence",
        }

    def extract_field_statuses_with_llm(self, title: str, content: str, canonical_layout: Dict[str, List[int]]) -> Dict:
        """
        Returns:
          {
            "parks": {
              "Palmer": {"1": "open|closed|unknown", ...},
              "Dublin": {"1": "open|closed|unknown", ...}
            },
            "confidence": float,
            "reason": "ok|disabled|missing_token|http_error|invalid_response|..."
          }
        """
        if not self.enabled:
            return {
                "parks": {},
                "confidence": 0.0,
                "reason": "disabled",
            }
        if not self.token:
            return {
                "parks": {},
                "confidence": 0.0,
                "reason": "missing_token",
            }

        all_fields = {
            park: [f"{park} {field_number}" for field_number in field_numbers]
            for park, field_numbers in canonical_layout.items()
        }

        system_prompt = (
            "You classify Madison park field status updates. "
            "Use the title and body together. "
            "Return strict JSON only with keys: parks, confidence. Confidence must be numeric 0..1. "
            "Do not use words like high, medium, or low for confidence. "
            "parks must contain Palmer and Dublin, each mapping allowed field numbers to one of "
            '"open", "closed", or "unknown". '
            "Use unknown when the field is not explicitly specified or the status is ambiguous. "
            "Do not infer closure/opening from season, weather, or context unless explicit. "
            "If all fields in a park are closed/open, mark every field in that park accordingly. "
            f"Allowed fields: {all_fields}. "
            "If the text says all fields at Palmer Park and Dublin Park are closed/open, mark all of them. "
            "Important: the words Extension or Palmer Extension refer to Palmer fields 7, 8, 9, and 10."
        )

        user_prompt = json.dumps(
            {
                "title": title,
                "content": content,
                "allowed_parks": canonical_layout,
                "all_fields": all_fields,
                "special_note": "Extension or Palmer Extension means Palmer fields 7, 8, 9, and 10.",
                "instructions": "Return the per-field status map for Palmer and Dublin. Confidence must be numeric 0..1.",
            }
        )

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

        req = urllib.request.Request(
            f"{self.endpoint}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw_resp = resp.read().decode("utf-8")
                if self._should_log_detail():
                    logger.info(
                        "LLM field-status HTTP %s bytes=%s",
                        getattr(resp, "status", "unknown"),
                        len(raw_resp),
                    )
        except urllib.error.HTTPError as e:
            logger.warning("LLM field-status HTTP error: %s", e)
            return {"parks": {}, "confidence": 0.0, "reason": "http_error"}
        except urllib.error.URLError as e:
            logger.warning("LLM field-status network error: %s", e)
            return {"parks": {}, "confidence": 0.0, "reason": "network_error"}
        except Exception as e:
            logger.warning("LLM field-status request error: %s", e)
            return {"parks": {}, "confidence": 0.0, "reason": "request_error"}

        try:
            payload = json.loads(raw_resp)
            content_text = payload["choices"][0]["message"]["content"]
            if self._should_log_trace():
                logger.info("LLM field-status response content preview: %s", _preview(content_text, 3000))
            parsed = json.loads(content_text)
        except Exception:
            if self._should_log_detail():
                logger.info("LLM field-status invalid JSON response. raw=%s", _preview(raw_resp, 2000))
            return {"parks": {}, "confidence": 0.0, "reason": "invalid_response"}

        allowed_states = {"open", "closed", "unknown"}
        normalized = {
            park: {str(field_number): "unknown" for field_number in field_numbers}
            for park, field_numbers in canonical_layout.items()
        }

        parks = parsed.get("parks", {})
        if isinstance(parks, dict):
            for park_name, field_map in parks.items():
                if park_name not in normalized or not isinstance(field_map, dict):
                    continue
                for field_key, state in field_map.items():
                    if str(field_key) not in normalized[park_name]:
                        continue
                    if isinstance(state, str) and state.lower() in allowed_states:
                        normalized[park_name][str(field_key)] = state.lower()

        try:
            confidence = float(parsed.get("confidence", 0.0))
        except Exception:
            confidence = 0.0
        confidence = _coerce_confidence(confidence)

        if self._should_log_detail():
            logger.info(
                "LLM field-status parsed confidence=%.2f reason=%s summary=%s",
                confidence,
                "ok" if confidence >= self.min_confidence else "low_confidence",
                _format_state_counts(normalized),
            )

        return {
            "parks": normalized,
            "confidence": confidence,
            "reason": "ok" if confidence >= self.min_confidence else "low_confidence",
        }
