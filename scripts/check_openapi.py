#!/usr/bin/env python
"""Generate the OpenAPI 3.1 snapshot and verify it matches the committed copy.

Usage:
    python scripts/check_openapi.py            # verify docs/openapi.json is current
    python scripts/check_openapi.py --write    # regenerate the snapshot
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs" / "openapi.json"


def build_document() -> dict[str, object]:
    sys.path.insert(0, str(ROOT / "src"))
    from vision_server.config import Settings
    from vision_server.runtime import Runtime
    from vision_server.transports.http import create_app

    with tempfile.TemporaryDirectory() as workdir:
        settings = Settings(
            _env_file=None,
            environment="development",
            auth_enabled=False,
            allowed_roots=workdir,
            asset_root=str(Path(workdir) / "assets"),
        )
        document: dict[str, object] = create_app(runtime=Runtime(settings)).openapi()
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="regenerate the snapshot")
    arguments = parser.parse_args()

    document = build_document()
    rendered = json.dumps(document, indent=2, sort_keys=True) + "\n"

    if arguments.write:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(rendered)
        print(f"wrote {SNAPSHOT.relative_to(ROOT)}")
        return 0

    if not SNAPSHOT.exists():
        print("docs/openapi.json is missing; run: python scripts/check_openapi.py --write")
        return 1
    if SNAPSHOT.read_text() != rendered:
        print("docs/openapi.json is stale; run: python scripts/check_openapi.py --write")
        return 1
    print("OpenAPI snapshot is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
