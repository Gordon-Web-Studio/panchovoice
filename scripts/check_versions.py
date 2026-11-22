"""Check that every package of PanchoVoiceAI has the same version.

Run from the repo root: python scripts/check_versions.py
Pass a version (for example 0.2.0) to also check that it matches: python scripts/check_versions.py 0.2.0
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def toml_version(path: Path) -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "missing"


def main() -> int:
    found = {
        "core/pyproject.toml": toml_version(ROOT / "core/pyproject.toml"),
        "core/src/panchovoice/__init__.py": (
            re.search(r'__version__\s*=\s*"([^"]+)"',
                      (ROOT / "core/src/panchovoice/__init__.py").read_text(encoding="utf-8")) or [None, "missing"]
        )[1],
        "demo/acme-agent/pyproject.toml": toml_version(ROOT / "demo/acme-agent/pyproject.toml"),
        "web/package.json": json.loads((ROOT / "web/package.json").read_text(encoding="utf-8")).get("version", "missing"),
    }
    expected = sys.argv[1].lstrip("v") if len(sys.argv) > 1 else found["core/pyproject.toml"]
    bad = {k: v for k, v in found.items() if v != expected}
    for k, v in found.items():
        print(f"{'ok ' if v == expected else 'BAD'} {v:10} {k}")
    if bad:
        print(f"Versions differ. Expected {expected} everywhere.")
        return 1
    print(f"All packages are at version {expected}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
