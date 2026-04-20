#!/usr/bin/env python3
"""
Nano Banana Pro image generation — multi-channel edition.

Generates images via Google Gemini 3 Pro Image Preview with automatic
fallback across three channels:
  1. AI Studio (direct, cheapest)
  2. Vertex AI Service Account (billing-independent)
  3. 柏拉图 bltcy.ai middleman (safety net)

Usage:
    python generate_image.py "prompt" [--resolution 2K] [--aspect 16:9]
                                      [--output ./generated_images]
                                      [--reference img.png]
                                      [--provider ai_studio,vertex_sa]
                                      [--only-provider vertex_sa]
                                      [--debug]

Credentials (priority order):
  1. ComfyUI Batchbox secrets.yaml
     (default: ~/Documents/ComfyUI/custom_nodes/ComfyUI-Custom-Batchbox/secrets.yaml
      or override with CCSKILL_SECRETS_YAML env)
  2. Skill directory .env (backward compatible with upstream)
  3. Process environment variables (GEMINI_API_KEY, VERTEX_SA_JSON_PATH, BLTCY_API_KEY)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Ensure sibling modules (config/, auth/, providers/) are importable
_SCRIPT_DIR = Path(__file__).parent.resolve()
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from config.loader import describe, load_credentials  # noqa: E402
from providers import (  # noqa: E402
    PermanentError,
    ProviderError,
    TransientError,
    build_provider_chain,
    generate_with_fallback,
)

# Defaults (backward compatible with upstream)
DEFAULT_RESOLUTION = "2K"
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_OUTPUT_DIR = "./generated_images"
DEFAULT_PROVIDER_ORDER = "ai_studio,vertex_sa,bltcy"

MIME_TO_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate images via Nano Banana Pro with multi-channel fallback"
    )
    parser.add_argument("prompt", type=str, help="Image generation prompt")
    parser.add_argument(
        "--resolution", type=str, default=DEFAULT_RESOLUTION,
        choices=["1K", "2K", "4K"],
        help=f"Output resolution (default: {DEFAULT_RESOLUTION})",
    )
    parser.add_argument(
        "--aspect", type=str, default=DEFAULT_ASPECT_RATIO,
        help=f"Aspect ratio (default: {DEFAULT_ASPECT_RATIO})",
    )
    parser.add_argument(
        "--output", type=str, default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--reference", type=str, action="append", default=[],
        help="Reference image path (repeatable, max 14)",
    )
    parser.add_argument(
        "--provider", type=str, default=DEFAULT_PROVIDER_ORDER,
        help=("Comma-separated provider priority order "
              f"(default: {DEFAULT_PROVIDER_ORDER})"),
    )
    parser.add_argument(
        "--only-provider", type=str, default=None,
        help="Use only one provider (no fallback). Overrides --provider.",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Show credential source + provider chain + fallback trace",
    )
    return parser.parse_args(args)


def get_output_path(output_dir: str, mime_type: str = "image/png") -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = MIME_TO_EXT.get(mime_type, ".png")
    return out / f"{ts}{ext}"


def main() -> int:
    args = parse_args()

    if args.reference and len(args.reference) > 14:
        print("[Error] Max 14 reference images", file=sys.stderr)
        return 1

    creds = load_credentials(skill_dir=_SCRIPT_DIR)
    if args.debug:
        print(f"[debug] credentials: {describe(creds)}")

    # Build provider chain
    if args.only_provider:
        order = [args.only_provider.strip()]
    else:
        order = [p.strip() for p in args.provider.split(",") if p.strip()]

    chain = build_provider_chain(creds, order=order)
    if args.debug or not chain:
        print(f"[debug] provider chain: {[p.name for p in chain]}")

    if not chain:
        print(
            "[Error] No providers available. Please configure at least one of:\n"
            "  - GEMINI_API_KEY (AI Studio)\n"
            "  - Vertex SA JSON (via secrets.yaml gcs.credentials or VERTEX_SA_JSON_PATH env)\n"
            "  - BLTCY_API_KEY (柏拉图 middleman)",
            file=sys.stderr,
        )
        return 1

    try:
        result = generate_with_fallback(
            providers=chain,
            prompt=args.prompt,
            resolution=args.resolution,
            aspect_ratio=args.aspect,
            reference_image_paths=args.reference or None,
            verbose=args.debug,
        )
    except PermanentError as e:
        print(f"[Error] Permanent failure from {e.provider}: {e}", file=sys.stderr)
        return 2
    except TransientError as e:
        msg = str(e)
        print(f"[Error] All providers failed: {msg}", file=sys.stderr)
        if "denied access" in msg.lower():
            print(
                "[Hint] This usually means GCP billing is suspended. "
                "Check https://console.cloud.google.com/billing",
                file=sys.stderr,
            )
        return 3
    except ProviderError as e:
        print(f"[Error] Provider error: {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"[Error] Unexpected: {type(e).__name__}: {e}", file=sys.stderr)
        return 4

    if result.text:
        print(f"[Info] {result.text}")

    output_path = get_output_path(args.output, result.mime_type)
    output_path.write_bytes(result.image_bytes)
    print(f"[Success] Image saved: {output_path}  "
          f"(via {result.provider}, {len(result.image_bytes)//1024}KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
