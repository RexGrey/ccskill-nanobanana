"""
柏拉图 (bltcy.ai) middleman provider.

OpenAI-compatible `/v1/images/generations`. Returns image URL; we fetch the bytes.
Uses model alias `nano-banana-2` which the proxy maps to Gemini 3 Pro Image.
"""

from __future__ import annotations

import random

import requests

from config.loader import Credentials
from .base import (
    GenerationResult,
    PermanentError,
    Provider,
    ProviderUnavailable,
    TransientError,
)

# Bltcy model name mapping. Can be overridden via resolution tier.
_MODEL_BY_RESOLUTION = {
    "1K": "nano-banana-2",
    "2K": "nano-banana-2-2k",
    "4K": "nano-banana-2-4k",
}

# OpenAI DALL-E style size mapping (bltcy accepts these)
_SIZE_BY_ASPECT = {
    "1:1": "1024x1024",
    "16:9": "1792x1024",
    "9:16": "1024x1792",
}


def _aspect_to_size(aspect_ratio: str, default: str = "1024x1024") -> str:
    return _SIZE_BY_ASPECT.get(aspect_ratio, default)


class BltcyProvider(Provider):
    name = "bltcy"

    def __init__(self, creds: Credentials, timeout: int = 180):
        self.creds = creds
        self.timeout = timeout

    def is_available(self) -> bool:
        return self.creds.has_bltcy()

    def generate(
        self,
        prompt: str,
        resolution: str,
        aspect_ratio: str,
        reference_image_paths: list[str] | None = None,
    ) -> GenerationResult:
        if not self.is_available():
            raise ProviderUnavailable(
                "No 柏拉图 (bltcy) API keys in credentials", provider=self.name
            )

        # Note: reference image editing via bltcy goes through a different
        # endpoint (/v1/images/edits, multipart). For now we use t2i only;
        # reference images cause fallback to next provider.
        if reference_image_paths:
            raise TransientError(
                "bltcy t2i path doesn't support reference images yet; "
                "use Vertex or AI Studio for i2i",
                provider=self.name,
            )

        model = _MODEL_BY_RESOLUTION.get(resolution, "nano-banana-2")
        size = _aspect_to_size(aspect_ratio)

        keys = list(self.creds.bltcy_keys)
        random.shuffle(keys)

        last_error: Exception | None = None
        for key in keys:
            url = f"{self.creds.bltcy_base_url.rstrip('/')}/v1/images/generations"
            payload = {"model": model, "prompt": prompt, "n": 1, "size": size}
            try:
                resp = requests.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                last_error = TransientError(
                    f"Network error: {e}", provider=self.name
                )
                continue

            try:
                data = resp.json()
            except ValueError:
                last_error = TransientError(
                    f"Non-JSON (HTTP {resp.status_code}): {resp.text[:200]}",
                    status_code=resp.status_code, provider=self.name,
                )
                continue

            if "error" in data:
                msg = str(data["error"])[:300]
                if resp.status_code == 401:
                    last_error = PermanentError(
                        f"Bltcy auth failed: {msg}",
                        status_code=401, provider=self.name,
                    )
                elif resp.status_code == 400:
                    raise PermanentError(
                        f"Bad request: {msg}",
                        status_code=400, provider=self.name, raw=data,
                    )
                else:
                    last_error = TransientError(
                        f"Bltcy error (HTTP {resp.status_code}): {msg}",
                        status_code=resp.status_code, provider=self.name, raw=data,
                    )
                continue

            if "data" not in data or not data["data"]:
                last_error = TransientError(
                    f"No data in response: {str(data)[:200]}",
                    provider=self.name, raw=data,
                )
                continue

            img_url = data["data"][0].get("url")
            if not img_url:
                last_error = TransientError(
                    f"No URL in data[0]: {data['data'][0]}",
                    provider=self.name, raw=data,
                )
                continue

            # Download image
            try:
                img_resp = requests.get(img_url, timeout=self.timeout)
                img_resp.raise_for_status()
            except requests.RequestException as e:
                last_error = TransientError(
                    f"Failed to download {img_url}: {e}", provider=self.name
                )
                continue

            # Guess mime from URL extension (bltcy typically serves jpg)
            mime = "image/png" if img_url.lower().endswith(".png") else "image/jpeg"

            return GenerationResult(
                image_bytes=img_resp.content,
                mime_type=mime,
                provider=self.name,
            )

        if last_error:
            raise last_error
        raise TransientError("All 柏拉图 keys failed", provider=self.name)
