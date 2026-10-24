"""Check documented HTTP routes against the Flask route map.

Run from the repository root: ``python scripts/check_api_docs.py``.
Placeholder names are normalized, so ``<id>`` documents ``<student_id>``.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402


ROUTE = re.compile(r"\b(GET|POST|PATCH|PUT|DELETE)\s+(/[^\s`\]})]+)")
PARAMETER = re.compile(r"<[^>]+>")


def normalized(path: str) -> str:
    """Ignore names of route variables while preserving URL structure."""
    return PARAMETER.sub("<*>", path.split("?", 1)[0].rstrip(".,;:"))


def main() -> int:
    """Report stale documentation and undocumented JSON API routes."""
    text = (ROOT / "docs" / "API.md").read_text(encoding="utf-8")
    documented = {(method, normalized(path)) for method, path in ROUTE.findall(text)}
    registered = {(method, normalized(rule.rule))
                  for mode in ("testing", "development")
                  for rule in create_app(mode).url_map.iter_rules() for method in rule.methods
                  if method in {"GET", "POST", "PATCH", "PUT", "DELETE"}}
    stale = sorted(documented - registered)
    missing = sorted(pair for pair in registered - documented if pair[1].startswith("/api/"))
    for method, path in stale:
        print(f"STALE {method} {path}")
    for method, path in missing:
        print(f"MISSING {method} {path}")
    print(f"Checked {len(documented)} documented routes against {len(registered)} Flask routes.")
    return 1 if stale or missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
