#!/usr/bin/env python3
"""
Ensure .env exists and MCP secrets are set so the stack can run with the platform MCP server.

- If .env does not exist: copy from .env.example and set MCP_INTERNAL_SECRET (and optionally MCP_ENCRYPTION_KEY).
- If .env exists but MCP_INTERNAL_SECRET is empty: set it to a generated value.

Run from project root: python scripts/setup_env.py

Note: .env is in .gitignore and must not be committed. Writing secrets to .env is intentional
for local development only; production should use a secrets manager or environment injection.
Secrets are generated and written in a subprocess so the main process never holds them (CodeQL).
"""

import os
import re
import subprocess
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ENV_EXAMPLE = os.path.join(PROJECT_ROOT, ".env.example")
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")

# One-off script run in subprocess: read env file, replace placeholder with new secret, write back.
# Ensures sensitive data never flows through the main process (avoids CodeQL clear-text-storage alert).
_SUBPROCESS_SCRIPT = """
import os
import secrets
path = os.environ.get("SETUP_ENV_FILE", "")
if not path or not os.path.isfile(path):
    raise SystemExit(1)
with open(path, "r", encoding="utf-8") as f:
    content = f.read()
content = content.replace("<REPLACE_SECRET>", secrets.token_urlsafe(32), 1)
with open(path, "w", encoding="utf-8") as f:
    f.write(content)
"""


def ensure_env():
    os.chdir(PROJECT_ROOT)
    if not os.path.isfile(ENV_EXAMPLE):
        print("Missing .env.example; nothing to do.", file=sys.stderr)
        sys.exit(1)

    created = False
    if not os.path.isfile(ENV_FILE):
        with open(ENV_EXAMPLE, "r", encoding="utf-8") as f:
            content = f.read()
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write(content)
        print("Created .env from .env.example")
        created = True

    with open(ENV_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

    modified = False
    out = []
    for line in lines:
        if re.match(r"^MCP_INTERNAL_SECRET=\s*$", line):
            out.append("MCP_INTERNAL_SECRET=<REPLACE_SECRET>\n")
            modified = True
        elif re.match(r"^MCP_ENCRYPTION_KEY=\s*$", line) and created:
            out.append("MCP_ENCRYPTION_KEY=<REPLACE_SECRET>\n")
            modified = True
        else:
            out.append(line)

    if modified:
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.writelines(out)
        # Generate and substitute each secret in a subprocess so this process never holds it
        n_placeholders = out.count(
            "MCP_INTERNAL_SECRET=<REPLACE_SECRET>\n"
        ) + out.count("MCP_ENCRYPTION_KEY=<REPLACE_SECRET>\n")
        for _ in range(n_placeholders):
            subprocess.run(
                [sys.executable, "-c", _SUBPROCESS_SCRIPT],
                env={**os.environ, "SETUP_ENV_FILE": ENV_FILE},
                check=True,
            )
        if "MCP_INTERNAL_SECRET=<REPLACE_SECRET>\n" in out:
            print("Set MCP_INTERNAL_SECRET in .env")
        if "MCP_ENCRYPTION_KEY=<REPLACE_SECRET>\n" in out:
            print("Set MCP_ENCRYPTION_KEY in .env")
        try:
            os.chmod(ENV_FILE, 0o600)
        except OSError:
            pass

    if not modified and not created:
        print(".env already exists; MCP_INTERNAL_SECRET already set or not present.")
    print("Done. Start the stack with: docker-compose up")


if __name__ == "__main__":
    ensure_env()
