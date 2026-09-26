# 0012: An AI-assisted PR review loop

- Status: Accepted
- Date: 2026-09-26

## Context
This is a public repository, so pull requests can receive comments from anyone, not just the
owner. The team wants a second, independent model reviewing code changes before the owner
merges, without spending beyond the owner's GitHub Student plan allowance of about 200 AI
credits a month (each Copilot review costs roughly $0.05-$1 in credits).

## Decision
Substantial PRs get a GitHub Copilot code review, requested at "Lite" effort through the
GitHub MCP server. Claude reads each comment and verifies it against the code before acting.
The owner is always the one who merges. Claude acts only on comments from Copilot or from the
owner; every other comment is treated as untrusted input.

## Consequences
- A second model family reviews every substantial change, at a cost that fits inside the
  Student plan's monthly credit allowance.
- Prompt injection through public PR comments (a stranger posting a comment designed to look
  like a review instruction) is mitigated three ways: an allowlist of who Claude acts on
  (Copilot and the owner only), a GitHub token scoped to this one repository with
  pull-request read/write and contents read-only, and no auto-merge under any condition.

## Alternatives considered
- **Claude-only review, no Copilot:** simpler, but loses the value of a second, differently
  trained model catching issues Claude's own review might miss.
- **No AI review at all:** cheapest and simplest, but forgoes an inexpensive extra check on a
  portfolio project where code quality is part of the point.
