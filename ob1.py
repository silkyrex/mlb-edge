"""
ob1.py -- shared OB1 / Supabase thought capture helper for mlb-edge scripts.

Usage:
    from ob1 import ob1_push
    ob1_push("content string", {"type": "...", "agent": "..."})

Silently skips if SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, or OPENROUTER_API_KEY
are not set in the environment.
"""

import json
import os
import urllib.request
from pathlib import Path

# Load OB1 credentials -- checks shell env first, then ~/.config/credentials/ob1.env
_OB1_ENV = Path.home() / ".config" / "credentials" / "ob1.env"
if _OB1_ENV.exists():
    for _line in _OB1_ENV.read_text().splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
_SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
_OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")


def ob1_push(content: str, metadata: dict) -> bool:
    """Push a thought to OB1. Returns True if successful, False otherwise."""
    if not (_SUPABASE_URL and _SUPABASE_KEY and _OPENROUTER_KEY):
        return False

    try:
        emb_req = urllib.request.Request(
            "https://openrouter.ai/api/v1/embeddings",
            data=json.dumps({"model": "openai/text-embedding-3-small", "input": content}).encode(),
            headers={"Authorization": f"Bearer {_OPENROUTER_KEY}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(emb_req, timeout=20) as r:
            embedding = json.loads(r.read())["data"][0]["embedding"]
    except Exception:
        embedding = None

    payload = json.dumps({
        "p_content": content,
        "p_payload": {"metadata": {**metadata, "source": "mlb-edge", "era": "live"}},
    }).encode()

    try:
        upsert_req = urllib.request.Request(
            f"{_SUPABASE_URL}/rest/v1/rpc/upsert_thought",
            data=payload,
            headers={
                "apikey": _SUPABASE_KEY,
                "Authorization": f"Bearer {_SUPABASE_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(upsert_req, timeout=15) as r:
            result = json.loads(r.read())

        if embedding and result and result.get("id"):
            urllib.request.urlopen(urllib.request.Request(
                f"{_SUPABASE_URL}/rest/v1/thoughts?id=eq.{result['id']}",
                data=json.dumps({"embedding": embedding}).encode(),
                headers={
                    "apikey": _SUPABASE_KEY,
                    "Authorization": f"Bearer {_SUPABASE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                method="PATCH",
            ), timeout=15).read()

        return True
    except Exception:
        return False
