"""
AI Studio (generativelanguage.googleapis.com) provider.

Direct call to Gemini 3 Pro Image Preview using a simple API key
(URL param `?key=...`). Supports a pool of keys for load balancing.
"""

from __future__ import annotations

import random

import requests

from config.loader import Credentials
from ._common import (
    build_gemini_parts,
    extract_image_from_gemini_response,
    format_error_message,
)
from .base import (
    GenerationResult,
    PermanentError,
    Provider,
    ProviderUnavailable,
    TransientError,
    classify_google_error,
)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_MODEL = "gemini-3-pro-image-preview"


class AIStudioProvider(Provider):
    name = "ai_studio"

    def __init__(self, creds: Credentials, timeout: int = 120):
        self.creds = creds
        self.timeout = timeout

    def is_available(self) -> bool:
        return self.creds.has_ai_studio()

    def generate(
        self,
        prompt: str,
        resolution: str,
        aspect_ratio: str,
        reference_image_paths: list[str] | None = None,
    ) -> GenerationResult:
        if not self.is_available():
            raise ProviderUnavailable(
                "No AI Studio API keys in credentials", provider=self.name
            )

        # Load-balance across available keys
        keys = list(self.creds.ai_studio_keys)
        random.shuffle(keys)

        last_error: Exception | None = None
        for key in keys:
            url = f"{_BASE_URL}/models/{_MODEL}:generateContent?key={key}"
            payload = {
                "contents": [{
                    "role": "user",
                    "parts": build_gemini_parts(prompt, reference_image_paths),
                }],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": {
                        "aspectRatio": aspect_ratio,
                        "imageSize": resolution,
                    },
                },
            }
            try:
                resp = requests.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                last_error = TransientError(
                    f"Network error: {e}", provider=self.name
                )
                continue

            # Parse body
            try:
                data = resp.json()
            except ValueError:
                last_error = TransientError(
                    f"Non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}",
                    status_code=resp.status_code, provider=self.name,
                )
                continue

            if "error" in data:
                code, msg = format_error_message(data)
                err_cls = classify_google_error(code or resp.status_code, msg)
                err = err_cls(msg, status_code=code, provider=self.name, raw=data)
                # Try next key on transient errors; abort on permanent
                if isinstance(err, PermanentError):
                    raise err
                last_error = err
                continue

            extracted = extract_image_from_gemini_response(data)
            if extracted is None:
                last_error = TransientError(
                    f"No image in response: {str(data)[:200]}",
                    provider=self.name, raw=data,
                )
                continue

            img_bytes, mime, text = extracted
            return GenerationResult(
                image_bytes=img_bytes,
                mime_type=mime,
                provider=self.name,
                text=text,
            )

        # All keys exhausted with transient errors
        if last_error:
            raise last_error
        raise TransientError("All AI Studio keys failed", provider=self.name)
