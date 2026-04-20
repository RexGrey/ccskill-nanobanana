"""Multi-channel providers for Nano Banana Pro image generation."""

from __future__ import annotations

from config.loader import Credentials
from .ai_studio import AIStudioProvider
from .base import (
    GenerationResult,
    PermanentError,
    Provider,
    ProviderError,
    ProviderUnavailable,
    TransientError,
)
from .bltcy import BltcyProvider
from .vertex_sa import VertexSAProvider

__all__ = [
    "Provider",
    "ProviderError",
    "ProviderUnavailable",
    "TransientError",
    "PermanentError",
    "GenerationResult",
    "AIStudioProvider",
    "VertexSAProvider",
    "BltcyProvider",
    "build_provider_chain",
    "generate_with_fallback",
]


def build_provider_chain(
    creds: Credentials,
    order: list[str] | None = None,
) -> list[Provider]:
    """
    Build the provider chain honoring priority order.

    Default order: ai_studio → vertex_sa → bltcy
      (AI Studio: cheapest + fastest when healthy)
      (Vertex SA: billing-independent fallback for AI Studio suspend)
      (Bltcy: middleman safety net if both Google channels fail)

    Override via `order`: list of provider names in desired sequence.
    Unavailable providers (no credentials) are silently dropped.
    """
    registry = {
        "ai_studio": AIStudioProvider,
        "vertex_sa": VertexSAProvider,
        "bltcy": BltcyProvider,
    }
    order = order or ["ai_studio", "vertex_sa", "bltcy"]

    chain: list[Provider] = []
    for name in order:
        cls = registry.get(name)
        if cls is None:
            continue
        provider = cls(creds)
        if provider.is_available():
            chain.append(provider)
    return chain


def generate_with_fallback(
    providers: list[Provider],
    prompt: str,
    resolution: str,
    aspect_ratio: str,
    reference_image_paths: list[str] | None = None,
    verbose: bool = True,
) -> GenerationResult:
    """
    Try providers in order; fall back on TransientError; abort on PermanentError.

    Raises:
        PermanentError: if any provider raises it (bad prompt, etc.)
        TransientError: if all providers exhaust transient errors
        RuntimeError: if provider list is empty
    """
    if not providers:
        raise RuntimeError(
            "No providers available. Check credentials in "
            "secrets.yaml or .env."
        )

    transient_errors: list[tuple[str, str]] = []
    for p in providers:
        if verbose:
            print(f"[chain] trying provider: {p.name}")
        try:
            result = p.generate(prompt, resolution, aspect_ratio,
                                reference_image_paths)
            if verbose:
                print(f"[chain] ✅ success via {result.provider}")
            return result
        except ProviderUnavailable as e:
            if verbose:
                print(f"[chain] skip {p.name}: {e}")
            continue
        except PermanentError as e:
            if verbose:
                print(f"[chain] ❌ permanent error from {p.name}: {e}")
            raise
        except TransientError as e:
            if verbose:
                print(f"[chain] ⚠️  transient error from {p.name}: {e}")
            transient_errors.append((p.name, str(e)))
            continue

    # All providers exhausted
    summary = "; ".join(f"{n}: {m[:100]}" for n, m in transient_errors)
    raise TransientError(
        f"All providers failed with transient errors. "
        f"(Tip: check GCP billing status if you see "
        f"'project has been denied access'.) Details: {summary}"
    )
