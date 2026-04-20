"""
Vertex AI (aiplatform.googleapis.com) provider using Service Account auth.

Uses OAuth2 Bearer token obtained from a Service Account JSON.
Completely independent from AI Studio — survives AI Studio billing suspend.
"""

from __future__ import annotations

import requests

from auth.sa_token import get_access_token, get_project_id
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

_MODEL = "gemini-3-pro-image-preview"
# `global` supports Gemini 3 Pro Image Preview as of 2026-04; us-central1 fallback
_LOCATIONS = ["global", "us-central1"]


class VertexSAProvider(Provider):
    name = "vertex_sa"

    def __init__(self, creds: Credentials, timeout: int = 180):
        self.creds = creds
        self.timeout = timeout

    def is_available(self) -> bool:
        return self.creds.has_vertex_sa() and bool(self.creds.vertex_project_id)

    def generate(
        self,
        prompt: str,
        resolution: str,
        aspect_ratio: str,
        reference_image_paths: list[str] | None = None,
    ) -> GenerationResult:
        if not self.is_available():
            raise ProviderUnavailable(
                "Missing Vertex SA credentials or project_id",
                provider=self.name,
            )

        # Get OAuth2 token (cached)
        token = get_access_token(
            sa_dict=self.creds.vertex_sa_json_inline,
            sa_path=self.creds.vertex_sa_json_path,
        )
        if not token:
            raise ProviderUnavailable(
                "Failed to obtain Vertex SA access token", provider=self.name
            )

        project_id = self.creds.vertex_project_id or get_project_id(
            sa_dict=self.creds.vertex_sa_json_inline,
            sa_path=self.creds.vertex_sa_json_path,
        )

        last_error: Exception | None = None
        for location in _LOCATIONS:
            url = (
                f"https://aiplatform.googleapis.com/v1/"
                f"projects/{project_id}/locations/{location}/"
                f"publishers/google/models/{_MODEL}:generateContent"
            )
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
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                last_error = TransientError(
                    f"Network error @ {location}: {e}", provider=self.name
                )
                continue

            try:
                data = resp.json()
            except ValueError:
                last_error = TransientError(
                    f"Non-JSON @ {location} (HTTP {resp.status_code}): "
                    f"{resp.text[:200]}",
                    status_code=resp.status_code, provider=self.name,
                )
                continue

            if "error" in data:
                code, msg = format_error_message(data)
                err_cls = classify_google_error(code or resp.status_code, msg)
                err = err_cls(msg, status_code=code, provider=self.name, raw=data)
                # 404 at one location often means model not deployed there → try next
                if code == 404:
                    last_error = err
                    continue
                if isinstance(err, PermanentError):
                    raise err
                last_error = err
                continue

            extracted = extract_image_from_gemini_response(data)
            if extracted is None:
                last_error = TransientError(
                    f"No image @ {location}: {str(data)[:200]}",
                    provider=self.name, raw=data,
                )
                continue

            img_bytes, mime, text = extracted
            return GenerationResult(
                image_bytes=img_bytes,
                mime_type=mime,
                provider=f"{self.name}:{location}",
                text=text,
            )

        if last_error:
            raise last_error
        raise TransientError("All Vertex locations failed", provider=self.name)
