# 0008: Bedrock with an eval-selected model behind a provider interface

- Status: Accepted
- Date: 2026-09-26

## Context
AI triage needs an LLM call that is cheap enough to run automatically on every finding, does
not require managing a provider API key, and can be swapped without changing application
code. Quality has to clear a bar before a model is trusted in production: the eval gates
require a schema-valid rate of at least 99%, technique accuracy of at least 90%, zero
grounding violations, and insufficient-evidence correctness of at least 80%.

## Decision
One provider interface (`generate_structured`) with three implementations: Bedrock, Ollama
and a fake for tests. Candidate models are gpt-oss-20b, Ministral 3 8B and Claude Haiku 4.5;
the eval suite runs against every candidate, and the default in production is the cheapest
one that passes all gates. Bedrock's structured-output mode is used throughout, and IAM auth
means no provider API key exists anywhere.

## Consequences
- Cost is about $0.0002 per explanation on gpt-oss-20b, well inside the AI budget.
- No vendor lock-in: swapping the default model, or adding a candidate, is a config change
  behind the same interface.
- An eval suite is now a required gate before any model change ships, which is ongoing
  maintenance but is also the only thing standing between a cheap model and a wrong one.

## Alternatives considered
- **Free API tiers outside Bedrock:** Gemini's free tier trains on submitted prompts and is
  barred for EEA/UK users; Groq's free tier has daily request caps that would throttle
  triage during normal use.
- **Local-only models:** would avoid API cost entirely, but the dev laptop's GPU has only
  2 GB of memory, too little to run a model of useful quality.
