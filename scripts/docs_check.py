#!/usr/bin/env python3
"""Check that docs/configuration.md and .env.example match config.ENV_VARS.

Fails (exit 1) if a variable is missing, undocumented, or documented with a
different default. Run from anywhere::

    backend/.venv/bin/python scripts/docs_check.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from soroban_indexer.config import ENV_VARS  # noqa: E402

UNSET = "*(unset)*"
ROW_RE = re.compile(r"^\|\s*`([A-Z0-9_]+)`\s*\|\s*([^|]+?)\s*\|")
ENV_RE = re.compile(r"^([A-Z0-9_]+)=(.*)$")


def documented_defaults(text: str) -> dict[str, str]:
    """Variable -> default from the markdown table (unset rendered as empty)."""
    out = {}
    for line in text.splitlines():
        m = ROW_RE.match(line)
        if m:
            raw = m.group(2)
            out[m.group(1)] = "" if raw == UNSET else raw.strip("`")
    return out


def example_defaults(text: str) -> dict[str, str]:
    """Variable -> value from the uncommented lines of .env.example."""
    out = {}
    for line in text.splitlines():
        m = ENV_RE.match(line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def compare(source: str, found: dict[str, str]) -> list[str]:
    """List mismatches between ``found`` and ENV_VARS."""
    errors = []
    for name, default in ENV_VARS.items():
        if name not in found:
            errors.append(f"{source}: {name} is missing")
        elif found[name] != default:
            errors.append(f"{source}: {name} default is {found[name]!r}, code says {default!r}")
    errors += [f"{source}: {name} is not a known variable" for name in found.keys() - ENV_VARS]
    return errors


def main() -> int:
    """Run the checks and print a report."""
    errors = compare(
        "docs/configuration.md",
        documented_defaults((ROOT / "docs" / "configuration.md").read_text()),
    )
    errors += compare(".env.example", example_defaults((ROOT / ".env.example").read_text()))
    for err in errors:
        print(f"ERROR {err}")
    if not errors:
        print(f"docs OK: {len(ENV_VARS)} variables documented consistently")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
