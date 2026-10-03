"""The API's OpenAPI document (spec §7, §11.4).

The repository keeps it at `frontend/openapi.json`, and the frontend's typed client is generated
from it: a change to the API shows in the pull request's diff, and the frontend stops compiling
until it handles the change. After changing the API, run `just openapi`.

Usage, from `backend/`:
    python -m nettriage.entrypoints.api.openapi_document ../frontend/openapi.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

from nettriage.entrypoints.api.app import create_app
from nettriage.entrypoints.api.services import Services
from nettriage.platform.config import Settings

# The API's version, not a build's: the document changes only when the API does.
API_VERSION = "1"


def openapi_document() -> dict[str, Any]:
    """Building the document calls no service, so the app is given none."""
    app = create_app(Settings(stage="local", version=API_VERSION), cast(Services, None))
    return app.openapi()


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> None:
    out = Path(argv[1])
    out.write_text(render(openapi_document()), encoding="utf-8", newline="\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv)
