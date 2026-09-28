"""`python -m tools.scenarios report|write|list`: measure the detectors or write one scenario."""

from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path

from tools.scenarios.catalog import FAMILIES, build, suite
from tools.scenarios.evaluate import report, score

DEFAULT_SEEDS = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.scenarios")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("report", help="score every detector on the generated suite")
    run.add_argument("--seeds", type=int, default=DEFAULT_SEEDS)
    run.add_argument("--out", type=Path, help="also write the Markdown report here")
    write = commands.add_parser("write", help="write one scenario as a flow log file")
    write.add_argument("family", choices=sorted(FAMILIES))
    write.add_argument("--seed", type=int, default=1)
    write.add_argument("--out", type=Path, required=True)
    write.add_argument("--gzip", action="store_true")
    commands.add_parser("list", help="list the scenario families")
    args = parser.parse_args(argv)

    if args.command == "list":
        print("\n".join(sorted(FAMILIES)))
        return 0
    if args.command == "write":
        data = build(args.family, args.seed).text().encode()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(gzip.compress(data, mtime=0) if args.gzip else data)
        print(f"Wrote {args.out}")
        return 0

    scenarios = suite(args.seeds)
    scores = score(scenarios)
    text = report(scores, len(scenarios), sum(len(s.records) for s in scenarios))
    print(text, end="")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    return 0 if all(s.passed for s in scores.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
