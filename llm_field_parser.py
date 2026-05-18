import json
import logging
import os
import urllib.error
import urllib.request
from typing import Dict, List

logger = logging.getLogger(__name__)


class GitHubModelsFieldParser:
    """Partial-status closed-field extraction via GitHub Models."""

    def __init__(self):
        self.enabled = os.getenv("LLM_PARTIAL_PARSE_ENABLED", "false").lower() == "true"
        self.token = os.getenv("GITHUB_MODELS_TOKEN") or os.getenv("GITHUB_TOKEN")
        self.endpoint = os.getenv(
            "GITHUB_MODELS_ENDPOINT", "https://models.github.ai/inference"
        ).rstrip("/")
        self.model = os.getenv("LLM_MODEL", "openai/gpt-4.1-mini")
        self.timeout_seconds = int(os.getenv("LLM_PARTIAL_PARSE_TIMEOUT_SECONDS", "12"))
        self.min_confidence = float(os.getenv("LLM_PARTIAL_MIN_CONFIDENCE", "0.65"))

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
            "Return strict JSON only with keys: closed_fields (array of strings), confidence (0..1). "
            "Use names from the allowed canonical list when possible."
        )
        user_prompt = json.dumps(
            {
                "content": content,
                "allowed_canonical_fields": canonical_fields,
                "instructions": "Extract only closed fields.",
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
            parsed = json.loads(content_text)
            raw_closed = parsed.get("closed_fields", [])
            confidence = float(parsed.get("confidence", 0.0))
            if not isinstance(raw_closed, list):
                raw_closed = []
        except Exception:
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

        return {
            "closed_fields": normalized,
            "confidence": confidence,
            "raw_closed_fields": raw_closed,
            "reason": "ok" if confidence >= self.min_confidence else "low_confidence",
        }
