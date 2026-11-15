"""Thin REST client for the FastAPI backend. The dashboard talks to the API only through this module."""
from __future__ import annotations

import os
from typing import Any

import requests


class ApiError(Exception):
    """An error response from the API (its {"error": {...}} envelope) or a connection problem."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


def base_url() -> str:
    return os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")


def clean(params: dict | None) -> dict:
    out = {}
    for k, v in (params or {}).items():
        if v is None or v == "":
            continue
        out[k] = str(v).lower() if isinstance(v, bool) else (v.isoformat() if hasattr(v, "isoformat") else v)
    return out


def get(path: str, params: dict | None = None, url: str | None = None, timeout: float = 120) -> Any:
    full = f"{(url or base_url())}{path}"
    try:
        r = requests.get(full, params=clean(params), timeout=timeout)
    except requests.RequestException as exc:
        raise ApiError(0, "UNREACHABLE", f"cannot reach the API at {url or base_url()} ({type(exc).__name__})") from exc
    if r.status_code >= 400:
        try:
            e = r.json()["error"]
            raise ApiError(r.status_code, e["code"], e["message"])
        except (ValueError, KeyError):
            raise ApiError(r.status_code, "HTTP_ERROR", r.text[:200]) from None
    return r.json()
