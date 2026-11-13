#!/usr/bin/env python3
"""Shrink state.json to what the Wyndham login needs and pack it for a GitHub secret.

GitHub secrets max out at 48 KB. A raw Playwright session is mostly
third-party ad/analytics cookies, so we keep only Wyndham and Okta cookies
and storage, then gzip + base64 the result into state.secret.

    python pack_state.py                       # reads state.json, writes state.secret
    gh secret set WYNDHAM_STATE < state.secret
"""
import base64
import gzip
import json
import pathlib
import sys

KEEP = ("wyndham", "okta")
LIMIT = 48 * 1024


def keep(host: str) -> bool:
    host = host.lower()
    return any(k in host for k in KEEP)


def pack(src="state.json", dst="state.secret") -> int:
    state = json.loads(pathlib.Path(src).read_text())
    before = len(json.dumps(state))
    state["cookies"] = [c for c in state.get("cookies", []) if keep(c.get("domain", ""))]
    state["origins"] = [o for o in state.get("origins", []) if keep(o.get("origin", ""))]
    pathlib.Path(src).write_text(json.dumps(state))
    blob = base64.b64encode(gzip.compress(json.dumps(state).encode(), 9)).decode()
    pathlib.Path(dst).write_text(blob)
    print(f"{src}: {before:,} -> {len(json.dumps(state)):,} bytes "
          f"({len(state['cookies'])} cookies, {len(state['origins'])} origins); "
          f"{dst}: {len(blob):,} bytes")
    if len(blob) > LIMIT:
        sys.exit(f"Still over GitHub's 48 KB secret limit ({len(blob):,} bytes).")
    return len(blob)


if __name__ == "__main__":
    pack()
