"""Cached access to the API for Streamlit pages."""
from __future__ import annotations

import json

import streamlit as st

from frontend import api_client
from frontend.api_client import ApiError

TTL = 20  # seconds: short enough to show new data quickly, long enough to avoid re-asking on every widget click


@st.cache_data(ttl=TTL, show_spinner=False)
def _cached(url: str, path: str, params_json: str):
    return api_client.get(path, json.loads(params_json), url=url)


def api(path: str, **params):
    """GET `path` (cached); on failure show the API's error message and stop the page."""
    try:
        return _cached(api_client.base_url(), path, json.dumps(api_client.clean(params), sort_keys=True, default=str))
    except ApiError as exc:
        st.error(f"API request failed: **{exc.code}** - {exc.message}")
        st.stop()


def fetch_all(path: str, limit: int = 2000, **params) -> tuple[list[dict], int]:
    """Collect up to `limit` rows over several pages; returns (rows, total available)."""
    rows, page, total = [], 1, 0
    while len(rows) < limit:
        body = api(path, page=page, page_size=min(500, limit - len(rows)), **params)
        total = body["total"]
        rows += body["items"]
        if page >= body["pages"]:
            break
        page += 1
    return rows, total
