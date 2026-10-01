"""Prompts that ship with the code (spec §8.3), versioned by file: `triage/v1.md` is prompt v1.
A new version is a new file, so stored analyses keep naming the prompt that produced them."""

from __future__ import annotations

from functools import cache
from importlib.resources import files


@cache
def triage_prompt(version: str) -> str:
    return files(__name__).joinpath("triage", f"{version}.md").read_text(encoding="utf-8")
