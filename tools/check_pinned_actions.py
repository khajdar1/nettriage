"""Fail if a GitHub Actions workflow uses an action that isn't pinned to a full commit SHA.

Usage: python tools/check_pinned_actions.py [.github/workflows]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

USES = re.compile(r"^\s*(?:-\s*)?uses:\s*['\"]?([^'\"\s#]+)", re.MULTILINE)
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def unpinned_actions(workflow_text: str) -> list[str]:
    return [
        ref
        for ref in USES.findall(workflow_text)
        if not ref.startswith("./") and not PINNED.match(ref)
    ]


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]) if argv else Path(".github/workflows")
    problems = [
        f"{path}: {ref}"
        for path in sorted(root.glob("*.y*ml"))
        for ref in unpinned_actions(path.read_text(encoding="utf-8"))
    ]
    if problems:
        print("Actions must be pinned to a full commit SHA:\n  " + "\n  ".join(problems))
        return 1
    print(f"All actions in {root} are pinned.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
