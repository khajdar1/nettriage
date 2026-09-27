"""Fail if CI could reach AWS: CI holds no cloud access (spec Revision 2, D3; ADR 0013).

Usage: python tools/check_no_cloud_access.py [repository root]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WORKFLOW_RULES: dict[str, re.Pattern[str]] = {
    # Matches the block-mapping form (`id-token: write`), quoted values (`id-token: "write"` /
    # `id-token: 'write'`) and the flow-mapping form (`{ id-token: write }`).
    "requests an OIDC token (id-token: write)": re.compile(r"id-token:\s*['\"]?write['\"]?\b"),
    # `permissions: write-all` grants every permission, including id-token: write.
    "grants id-token: write (permissions: write-all)": re.compile(r"^\s*permissions:\s*write-all\b", re.MULTILINE),
    "uses an AWS action": re.compile(r"uses:\s*['\"]?aws-actions/"),
    "references AWS credentials": re.compile(
        r"\bAWS_(?:ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN)\b|role-to-assume"
    ),
}
TERRAFORM_RULES: dict[str, re.Pattern[str]] = {
    "defines an OIDC identity provider": re.compile(r'resource\s+"aws_iam_openid_connect_provider"'),
    "trusts GitHub's OIDC issuer": re.compile(r"token\.actions\.githubusercontent\.com"),
}


def problems_in(text: str, rules: dict[str, re.Pattern[str]]) -> list[str]:
    return [label for label, pattern in rules.items() if pattern.search(text)]


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]) if argv else Path(".")
    found = [
        f"{path}: {problem}"
        for path in sorted((root / ".github" / "workflows").glob("*.y*ml"))
        for problem in problems_in(path.read_text(encoding="utf-8"), WORKFLOW_RULES)
    ]
    found += [
        f"{path}: {problem}"
        for path in sorted((root / "infra").rglob("*.tf"))
        if ".terraform" not in path.parts
        for problem in problems_in(path.read_text(encoding="utf-8"), TERRAFORM_RULES)
    ]
    if found:
        print("CI must hold no cloud access (ADR 0013):\n  " + "\n  ".join(found))
        return 1
    print("CI holds no cloud access.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
