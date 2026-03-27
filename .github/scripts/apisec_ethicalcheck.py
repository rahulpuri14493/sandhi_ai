#!/usr/bin/env python3
"""
APIsec EthicalCheck helpers for GitHub Actions (stdlib only).

- preflight: verify JWT login without shell/jq mangling passwords ($, quotes, newlines).
- prepare-scan: write login JSON and patch apisec-run-scan.sh v1.0.7 to POST -d @file and /auth/login.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

# apisec-run-scan.sh v1.0.7 line 170 (exact)
VENDOR_LOGIN_LINE = (
    'tokenResp=$(curl -s -H "Content-Type: application/json" -X POST -d '
    '\'{"username": "\'${FX_USER}\'", "password": "\'${FX_PWD}\'"}\' ${FX_HOST}/login )'
)


def _login_json_path() -> str:
    return os.path.join(os.environ.get("GITHUB_WORKSPACE", "."), ".apisec_login.json")


def _try_jwt_login(host: str, path: str, body: bytes, headers: dict) -> tuple[int, dict | None]:
    url = f"{host}{path}"
    print(f"Trying APIsec JWT login: {url}")
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = resp.getcode()
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        code = e.code
    print(f"HTTP status: {code}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("Non-JSON response; preview (240 chars):", file=sys.stderr)
        print(raw[:240], file=sys.stderr)
        return code, None
    return code, data


def cmd_preflight() -> int:
    host = (os.environ.get("APISEC_HOST") or "https://cloud.apisec.ai").rstrip("/")
    user = os.environ.get("APISEC_USERNAME", "")
    password = os.environ.get("APISEC_PASSWORD", "")
    if not user or not password:
        print("APISEC_USERNAME and APISEC_PASSWORD must be set", file=sys.stderr)
        return 1

    body = json.dumps({"username": user, "password": password}).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "sandhi-ai-ethicalcheck-workflow",
    }

    for path in ("/auth/login", "/login"):
        _code, data = _try_jwt_login(host, path, body, headers)
        if data is None:
            continue
        if "token" in data:
            print(f"APIsec login OK via {path} (JWT in response).")
            return 0
        msg = data.get("message", "")
        print(f"No token from {path}: {msg}")

    print(
        "JWT login failed. Check APISEC_HOST (e.g. https://cloud.apisecapps.com), "
        "email/password for JSON /auth/login (SSO-only accounts may not work in CI), "
        "and that secrets have no stray whitespace.",
        file=sys.stderr,
    )
    return 1


def cmd_prepare_scan() -> int:
    """Write credentials JSON and patch vendor script in the current directory."""
    user = os.environ.get("APISEC_USERNAME", "")
    password = os.environ.get("APISEC_PASSWORD", "")
    if not user or not password:
        print("APISEC_USERNAME and APISEC_PASSWORD must be set", file=sys.stderr)
        return 1

    path = _login_json_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"username": user, "password": password}, f)
    os.chmod(path, 0o600)
    print(f"Wrote {path} (mode 0600).")

    script_path = os.path.join(os.getcwd(), "apisec-run-scan.sh")
    if not os.path.isfile(script_path):
        print(f"Missing {script_path}; download the script first.", file=sys.stderr)
        return 1

    text = open(script_path, encoding="utf-8", errors="replace").read()
    if VENDOR_LOGIN_LINE not in text:
        # Fallback: sed may have already changed /login to /auth/login
        alt = VENDOR_LOGIN_LINE.replace("/login )", "/auth/login )")
        if alt in text:
            old = alt
        else:
            print(
                "Could not find expected login line in apisec-run-scan.sh; "
                "vendor script version may have changed.",
                file=sys.stderr,
            )
            return 1
    else:
        old = VENDOR_LOGIN_LINE

    login_json = path
    new_line = (
        f'tokenResp=$(curl -s -H "Content-Type: application/json" -X POST -d @"{login_json}" '
        "${FX_HOST}/auth/login )"
    )
    patched = text.replace(old, new_line, 1)
    if patched == text:
        print("Patch did not change the file.", file=sys.stderr)
        return 1
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(patched)
    print("Patched apisec-run-scan.sh to use JSON file body and /auth/login.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preflight", help="Verify APIsec JWT login")
    sub.add_parser("prepare-scan", help="Write login JSON and patch apisec-run-scan.sh")
    args = p.parse_args()
    if args.cmd == "preflight":
        return cmd_preflight()
    return cmd_prepare_scan()


if __name__ == "__main__":
    raise SystemExit(main())
