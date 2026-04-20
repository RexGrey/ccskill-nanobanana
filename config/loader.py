"""
Credentials loader for multi-channel providers.

Resolution order:
  1. ComfyUI Batchbox `secrets.yaml` (if `CCSKILL_SECRETS_YAML` env or default path exists)
  2. Local `.env` in skill directory (backward compatible with upstream)
  3. Process environment variables (for CI/container use)

Supported credentials:
  - AI Studio API keys (list) — for `ai_studio` provider
  - Service Account JSON path/content — for `vertex_sa` provider
  - Bltcy (柏拉图) sk- keys (list) — for `bltcy` provider fallback
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


# Default ComfyUI Batchbox secrets path (macOS user install)
_DEFAULT_COMFYUI_SECRETS = Path.home() / (
    "Documents/ComfyUI/custom_nodes/ComfyUI-Custom-Batchbox/secrets.yaml"
)


@dataclass
class Credentials:
    """Loaded credentials, possibly empty per-channel."""
    ai_studio_keys: list[str] = field(default_factory=list)
    vertex_sa_json_path: str | None = None
    vertex_sa_json_inline: dict | None = None
    vertex_project_id: str | None = None
    bltcy_keys: list[str] = field(default_factory=list)
    bltcy_base_url: str = "https://api.bltcy.ai"
    source: str = "none"  # "secrets.yaml" | ".env+env" | "env_only"

    def pick_ai_studio_key(self) -> str | None:
        """Random pick to load-balance across keys."""
        return random.choice(self.ai_studio_keys) if self.ai_studio_keys else None

    def pick_bltcy_key(self) -> str | None:
        return random.choice(self.bltcy_keys) if self.bltcy_keys else None

    def has_ai_studio(self) -> bool:
        return bool(self.ai_studio_keys)

    def has_vertex_sa(self) -> bool:
        return bool(self.vertex_sa_json_path or self.vertex_sa_json_inline)

    def has_bltcy(self) -> bool:
        return bool(self.bltcy_keys)


def _load_from_secrets_yaml(path: Path) -> Credentials | None:
    """Parse ComfyUI Batchbox secrets.yaml; return None on any failure."""
    if not _YAML_AVAILABLE:
        return None
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return None

    creds = Credentials(source=f"secrets.yaml:{path}")
    providers = data.get("providers", {})

    # AI Studio keys (list of {key, name} or bare strings)
    g = providers.get("google_official", {})
    for item in g.get("api_keys", []):
        if isinstance(item, dict) and item.get("key"):
            creds.ai_studio_keys.append(item["key"])
        elif isinstance(item, str):
            creds.ai_studio_keys.append(item)

    # Service account JSON (inline under gcs.credentials)
    # Note: SA can only access its OWN project, so SA's project_id always wins
    # for SA-based calls. The `providers.vertex_ai.project_id` is only relevant
    # for Vertex Express API-key mode (not implemented here).
    gcs_creds = data.get("gcs", {}).get("credentials")
    if isinstance(gcs_creds, dict) and gcs_creds.get("private_key"):
        # Fill in fields google-auth may require
        if "auth_provider_x509_cert_url" not in gcs_creds:
            gcs_creds["auth_provider_x509_cert_url"] = (
                "https://www.googleapis.com/oauth2/v1/certs"
            )
        if "client_x509_cert_url" not in gcs_creds and gcs_creds.get("client_email"):
            ce = gcs_creds["client_email"].replace("@", "%40")
            gcs_creds["client_x509_cert_url"] = (
                f"https://www.googleapis.com/robot/v1/metadata/x509/{ce}"
            )
        creds.vertex_sa_json_inline = gcs_creds
        # SA's project_id takes precedence (SA can only access its own project)
        creds.vertex_project_id = gcs_creds.get("project_id")

    # Fallback to providers.vertex_ai.project_id only if SA didn't set one
    # (e.g., API-key-mode Vertex Express without a SA configured)
    if not creds.vertex_project_id:
        v = providers.get("vertex_ai", {})
        creds.vertex_project_id = v.get("project_id")

    # Bltcy (柏拉图) keys
    b = providers.get("柏拉图", {}) or providers.get("bltcy", {})
    if b.get("base_url"):
        creds.bltcy_base_url = b["base_url"]
    for item in b.get("api_keys", []):
        if isinstance(item, dict) and item.get("key"):
            creds.bltcy_keys.append(item["key"])
        elif isinstance(item, str):
            creds.bltcy_keys.append(item)

    return creds


def _load_from_env(dotenv_path: Path | None = None) -> Credentials:
    """Load from .env + process env. Backward compatible with upstream."""
    from dotenv import load_dotenv

    if dotenv_path and dotenv_path.exists():
        load_dotenv(dotenv_path)

    creds = Credentials(source=".env+env" if dotenv_path else "env_only")

    # AI Studio
    ai_key = os.environ.get("GEMINI_API_KEY")
    if ai_key:
        creds.ai_studio_keys.append(ai_key)
    # Extra comma-separated keys
    extra = os.environ.get("GEMINI_API_KEYS", "")
    for k in extra.split(","):
        k = k.strip()
        if k and k not in creds.ai_studio_keys:
            creds.ai_studio_keys.append(k)

    # Vertex SA
    sa_path = os.environ.get("VERTEX_SA_JSON_PATH")
    if sa_path:
        creds.vertex_sa_json_path = sa_path
        # Try to extract project_id
        try:
            with open(sa_path, "r") as f:
                sa_data = json.load(f)
                creds.vertex_project_id = sa_data.get("project_id")
        except Exception:
            pass
    creds.vertex_project_id = (
        creds.vertex_project_id or os.environ.get("VERTEX_PROJECT_ID")
    )

    # Bltcy
    bltcy_key = os.environ.get("BLTCY_API_KEY")
    if bltcy_key:
        creds.bltcy_keys.append(bltcy_key)
    creds.bltcy_base_url = os.environ.get(
        "BLTCY_BASE_URL", creds.bltcy_base_url
    )

    return creds


def load_credentials(
    skill_dir: Path | None = None,
    comfyui_secrets_path: str | Path | None = None,
) -> Credentials:
    """
    Load credentials with layered priority.

    Priority:
      1. ComfyUI `secrets.yaml` if present (via `CCSKILL_SECRETS_YAML` env or default path)
      2. `.env` in skill directory
      3. Process environment variables

    Credentials from (1) and (2) are MERGED: secrets.yaml wins for overlapping fields,
    but .env can fill in gaps (e.g., if secrets.yaml has no bltcy key).
    """
    # Resolve secrets.yaml path
    secrets_path = comfyui_secrets_path or os.environ.get("CCSKILL_SECRETS_YAML")
    secrets_path = Path(secrets_path) if secrets_path else _DEFAULT_COMFYUI_SECRETS

    yaml_creds = _load_from_secrets_yaml(secrets_path)

    # .env path (skill dir)
    dotenv_path = (skill_dir or Path(__file__).parent.parent) / ".env"
    env_creds = _load_from_env(dotenv_path)

    if yaml_creds is None:
        return env_creds

    # Merge: secrets.yaml is primary; .env fills gaps
    merged = yaml_creds
    if not merged.ai_studio_keys:
        merged.ai_studio_keys = env_creds.ai_studio_keys
    if not merged.has_vertex_sa():
        merged.vertex_sa_json_path = env_creds.vertex_sa_json_path
        merged.vertex_sa_json_inline = env_creds.vertex_sa_json_inline
    if not merged.vertex_project_id:
        merged.vertex_project_id = env_creds.vertex_project_id
    if not merged.bltcy_keys:
        merged.bltcy_keys = env_creds.bltcy_keys

    merged.source = f"{yaml_creds.source} + {env_creds.source}"
    return merged


def describe(creds: Credentials) -> str:
    """Human-readable summary (for diagnostics, no secrets)."""
    parts = [f"source={creds.source}"]
    parts.append(f"ai_studio_keys={len(creds.ai_studio_keys)}")
    parts.append(
        f"vertex_sa={'inline' if creds.vertex_sa_json_inline else (creds.vertex_sa_json_path or 'none')}"
    )
    parts.append(f"vertex_project={creds.vertex_project_id or 'none'}")
    parts.append(f"bltcy_keys={len(creds.bltcy_keys)}")
    return " ".join(parts)
