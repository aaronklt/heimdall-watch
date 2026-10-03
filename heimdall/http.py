import time

import requests

from heimdall import config

_session = requests.Session()
_session.headers["User-Agent"] = config.USER_AGENT


def get_json(url: str, params: dict | None = None, headers: dict | None = None, retries: int = 4):
    """GET JSON avec attente progressive sur les erreurs 429/5xx (limites des API gratuites)."""
    delay = 5
    for attempt in range(retries):
        resp = _session.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == retries - 1:
                resp.raise_for_status()
            time.sleep(delay)
            delay *= 2
            continue
        resp.raise_for_status()
        return resp.json()


def get_text(url: str) -> str:
    resp = _session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def post_json(url: str, payload: dict, headers: dict | None = None):
    resp = _session.post(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()
