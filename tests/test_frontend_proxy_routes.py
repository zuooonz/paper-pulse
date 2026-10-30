"""Every backend API prefix the frontend calls must be proxied by next.config.ts rewrites."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = ROOT / "frontend" / "src"
NEXT_CONFIG = ROOT / "frontend" / "next.config.ts"

# Routes served by Next.js itself (NextAuth and app route handlers).
NEXT_OWNED = {"auth", "papers/batch-generate-word-summary"}


def _called_prefixes() -> set[str]:
    prefixes = set()
    for path in FRONTEND_SRC.rglob("*.ts*"):
        for match in re.finditer(r'["\'`]/api/([A-Za-z0-9_-]+)', path.read_text(encoding="utf-8")):
            prefixes.add(match.group(1))
    return prefixes


def test_frontend_api_prefixes_are_proxied():
    config = NEXT_CONFIG.read_text(encoding="utf-8")
    proxied = set(re.findall(r'source:\s*"/api/([A-Za-z0-9_-]+)', config))
    missing = sorted(p for p in _called_prefixes() - proxied if p not in NEXT_OWNED)
    assert not missing, f"frontend calls /api/<prefix> without a rewrite: {missing}"
