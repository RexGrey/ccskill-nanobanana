"""Shared helpers for providers."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path


def read_reference_as_inline(image_path: str) -> dict:
    """Read a local image and return `{inlineData: {mimeType, data}}` part."""
    p = Path(image_path)
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return {
        "inlineData": {
            "mimeType": mime,
            "data": base64.b64encode(p.read_bytes()).decode("ascii"),
        }
    }


def build_gemini_parts(prompt: str, reference_image_paths: list[str] | None) -> list[dict]:
    """Build Gemini API `parts` list with prompt + (optional) reference images."""
    parts: list[dict] = []
    if reference_image_paths:
        for path in reference_image_paths:
            parts.append(read_reference_as_inline(path))
    parts.append({"text": prompt})
    return parts


def extract_image_from_gemini_response(data: dict) -> tuple[bytes, str, str | None] | None:
    """
    Extract image from a Gemini `generateContent` response.
    Returns (image_bytes, mime_type, accompanying_text) or None if not found.
    """
    text_acc = []
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            if "inlineData" in part:
                img = base64.b64decode(part["inlineData"]["data"])
                mime = part["inlineData"].get("mimeType", "image/png")
                return img, mime, "\n".join(text_acc) if text_acc else None
            if "text" in part:
                text_acc.append(part["text"])
    return None


def format_error_message(data: dict) -> tuple[int | None, str]:
    """Extract (status_code, message) from a Google API error response."""
    err = data.get("error", {})
    code = err.get("code")
    msg = err.get("message", "")
    status = err.get("status", "")
    return code, f"[{status}] {msg}" if status else msg
