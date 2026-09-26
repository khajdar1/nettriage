# 0007: Two queues: analyze and triage

- Status: Accepted
- Date: 2026-09-26

## Context
Parsing an upload and running detectors is fast, cheap and deterministic. Calling an LLM for
AI triage is slower, costs money, and can fail independently: Bedrock throttling, an
exhausted AI budget, or a bad model output. Coupling both stages in one consumer would mean
an LLM outage stalls detection too, and a crash during the slow stage would force reparsing
the fast one.

## Decision
An S3 upload event goes to SQS `analyze`, which triggers the analyze Lambda (parse, detect);
its output goes to SQS `triage`, which triggers the triage Lambda (budget check, Bedrock
call, validate). Both queues have a dead-letter queue with `maxReceiveCount` 3, and each
consumer's event source mapping caps maximum concurrency at 2.

## Consequences
- An LLM outage or an exhausted AI budget only stalls triage; findings are still detected and
  visible immediately.
- Retries after a worker crash or timeout never re-parse the uploaded file, since parsing and
  triage are separate messages.
- Each stage gets its own concurrency limit and its own alarms (DLQ depth, age of oldest
  message), so a problem in one stage is diagnosed independently of the other.

## Alternatives considered
- **One queue, one consumer doing both stages:** simpler to operate, but ties detection
  latency and availability to the LLM's, and a crash mid-triage means reparsing.
- **Step Functions:** 4,000 free state transitions a month would cover this workload, but
  adds a second orchestration layer and more moving parts for a two-stage pipeline that two
  queues already express cleanly.
