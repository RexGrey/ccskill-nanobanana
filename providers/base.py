"""
Abstract Provider interface and error taxonomy for multi-channel image generation.

Key design:
  - Providers return (image_bytes, mime_type) on success
  - Errors classified as:
      * ProviderUnavailable  — provider lacks credentials; skip silently
      * TransientError       — retryable; fall back to next provider (429, 5xx, billing suspend, network)
      * PermanentError       — non-retryable; abort (400 bad prompt, 413 too large)
  - Provider.name used for logging
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class ProviderError(Exception):
    """Base for all provider errors."""
    def __init__(self, message: str, *, status_code: int | None = None,
                 provider: str = "", raw: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider
        self.raw = raw


class ProviderUnavailable(ProviderError):
    """Provider has no credentials / is disabled. Skip silently."""


class TransientError(ProviderError):
    """Retryable error. Fall back to next provider."""


class PermanentError(ProviderError):
    """Non-retryable error. Abort the whole request."""


@dataclass
class GenerationResult:
    image_bytes: bytes
    mime_type: str
    provider: str
    text: str | None = None  # Optional accompanying text


class Provider(ABC):
    """Abstract base for image generation providers."""

    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if credentials / config allow this provider to run."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        resolution: str,
        aspect_ratio: str,
        reference_image_paths: list[str] | None = None,
    ) -> GenerationResult:
        """
        Generate a single image.

        Raises:
            ProviderUnavailable: if credentials are missing
            TransientError: for 429/5xx/network/billing-suspend
            PermanentError: for 400/413/bad prompt
        """


def classify_google_error(status_code: int | None, message: str) -> type[ProviderError]:
    """
    Classify a Google API error (AI Studio or Vertex) into error type.

    Billing suspend note:
      `"Your project has been denied access. Please contact support."` with 403/400
      can mean EITHER permanent denial OR temporary billing suspension. We classify
      as Transient so the chain falls back; the reporter then suggests billing check.
    """
    msg_lower = (message or "").lower()

    # Billing-suspend pattern → Transient (fall back + suggest billing check)
    if "project has been denied access" in msg_lower:
        return TransientError
    if "billing" in msg_lower and ("disabled" in msg_lower or "required" in msg_lower):
        return TransientError

    if status_code is None:
        return TransientError  # network/unknown → retry via fallback

    if status_code == 429:
        return TransientError
    if 500 <= status_code < 600:
        return TransientError
    if status_code == 403:
        # Most 403s (except billing suspend above) are permanent permission issues
        return PermanentError
    if status_code == 400:
        return PermanentError  # bad prompt / bad request
    if status_code == 413:
        return PermanentError  # payload too large

    return TransientError  # default to retry
