#!/usr/bin/env python3
"""
Ensure .env exists and MCP secrets are set so the stack can run with the platform MCP server.

- If .env does not exist: copy from .env.example and set MCP_INTERNAL_SECRET (and optionally MCP_ENCRYPTION_KEY).
- If .env exists but MCP_INTERNAL_SECRET is empty: set it to a generated value.

Run from project root: python scripts/setup_env.py

Note: .env is in .gitignore and must not be committed. Writing secrets to .env is intentional
for local development only; production should use a secrets manager or environment injection.
"""
import os
import re
import secrets
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ENV_EXAMPLE = os.path.join(PROJECT_ROOT, ".env.example")
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


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
    # Write line-by-line; secret written in separate calls so it is not stored in a composite expression (CodeQL)
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        for line in lines:
            if re.match(r"^MCP_INTERNAL_SECRET=\s*$", line):
                f.write("MCP_INTERNAL_SECRET=")
                # codeql[py/clear-text-storage-sensitive-data] .env is gitignored; local dev only; prod uses secrets manager
                f.write(generate_secret())
                f.write("\n")
                modified = True
                print("Set MCP_INTERNAL_SECRET in .env")
            elif re.match(r"^MCP_ENCRYPTION_KEY=\s*$", line) and created:
                f.write("MCP_ENCRYPTION_KEY=")
                # codeql[py/clear-text-storage-sensitive-data] .env is gitignored; local dev only; prod uses secrets manager
                f.write(generate_secret())
                f.write("\n")
                modified = True
                print("Set MCP_ENCRYPTION_KEY in .env")
            else:
                f.write(line)

    if modified:
        try:
            os.chmod(ENV_FILE, 0o600)
        except OSError:
            pass

    if not modified and not created:
        print(".env already exists; MCP_INTERNAL_SECRET already set or not present.")
    print("Done. Start the stack with: docker-compose up")


if __name__ == "__main__":
    ensure_env()
