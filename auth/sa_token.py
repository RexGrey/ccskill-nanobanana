"""
Service Account OAuth2 token generation for Vertex AI.

Adapted from ComfyUI-Custom-Batchbox/vertex_sa_auth.py (thread-safe cached token).
Produces `ya29.xxx` Bearer tokens from service_account JSON.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# Thread-local token cache keyed by credential fingerprint
_token_cache: dict[str, tuple[object, float]] = {}
_lock = threading.Lock()

# Refresh 5 min before expiry to avoid race conditions
_REFRESH_MARGIN = 300


def _cache_key_from_inline(sa_dict: dict) -> str:
    """Stable cache key from inline SA JSON (uses private_key_id)."""
    return f"inline:{sa_dict.get('private_key_id','unknown')}"


def _cache_key_from_path(path: str) -> str:
    return f"path:{os.path.abspath(path)}"


def _build_credentials(sa_dict: dict | None = None, sa_path: str | None = None):
    """Create google-auth Credentials object from either inline dict or file path."""
    try:
        from google.oauth2 import service_account
    except ImportError:
        raise RuntimeError(
            "google-auth not installed. Run: pip install google-auth"
        )

    if sa_dict is not None:
        return service_account.Credentials.from_service_account_info(
            sa_dict, scopes=_SCOPES
        )
    elif sa_path is not None:
        if not os.path.exists(sa_path):
            raise FileNotFoundError(f"Service account JSON not found: {sa_path}")
        return service_account.Credentials.from_service_account_file(
            sa_path, scopes=_SCOPES
        )
    else:
        raise ValueError("Either sa_dict or sa_path must be provided")


def get_access_token(
    sa_dict: dict | None = None,
    sa_path: str | None = None,
) -> Optional[str]:
    """
    Get a valid OAuth2 access token, auto-refresh if expired.

    Thread-safe. Cached per-credential (keyed by private_key_id or abs path).
    Returns the `ya29.xxx` token string, or None on failure.
    """
    if sa_dict is None and sa_path is None:
        return None

    cache_key = (
        _cache_key_from_inline(sa_dict) if sa_dict else _cache_key_from_path(sa_path)
    )

    with _lock:
        cached = _token_cache.get(cache_key)
        if cached is not None:
            creds, last_checked = cached
            try:
                if (creds.valid and creds.expiry and
                        creds.expiry.timestamp() > time.time() + _REFRESH_MARGIN):
                    return creds.token
            except Exception:
                pass  # fall through to refresh

    # Build or refresh credentials (network I/O outside lock)
    try:
        from google.auth.transport.requests import Request

        with _lock:
            cached = _token_cache.get(cache_key)
            if cached is not None:
                creds = cached[0]
            else:
                creds = _build_credentials(sa_dict, sa_path)
                _token_cache[cache_key] = (creds, time.time())

        creds.refresh(Request())
        return creds.token

    except Exception as e:
        print(f"[VertexSA] Token refresh failed: {e}")
        return None


def get_project_id(sa_dict: dict | None = None, sa_path: str | None = None) -> Optional[str]:
    """Extract project_id from service account credentials."""
    if sa_dict:
        return sa_dict.get("project_id")
    if sa_path and os.path.exists(sa_path):
        try:
            with open(sa_path, "r") as f:
                return json.load(f).get("project_id")
        except Exception:
            pass
    return None
