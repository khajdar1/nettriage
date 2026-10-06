"""The restore drill on demand (Plan 7a §2.5): the owner's session invokes the stage's ops
function once with `{"job": "restore_drill"}` and waits for its answer. The drill's own
failures are told as they are, since they name only a day and tables; any other failure is
named by its type, because its message could quote connection details: the function sends
`JobFailed` with only the type, and anything else (a timeout) is named by its own type."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from pathlib import Path

from tools.deploy import config
from tools.deploy.runner import CommandError, Runner

# The function may run for 600 s; the CLI waits a minute longer, and never retries, since a
# retry would start a second drill beside the first.
READ_TIMEOUT_SECONDS = 660
TOLD = ("DrillFailed", "NoBackup")


def restore_drill(run: Runner, env: Mapping[str, str], stage: str) -> str:
    function = config.ops_function(stage)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        answer_file = Path(scratch) / "answer.json"
        try:
            metadata = run(
                ["aws", "lambda", "invoke", "--function-name", function,
                 "--payload", json.dumps({"job": "restore_drill"}),
                 "--cli-binary-format", "raw-in-base64-out",
                 "--cli-read-timeout", str(READ_TIMEOUT_SECONDS),
                 "--output", "json", str(answer_file)],
                env={**env, "AWS_MAX_ATTEMPTS": "1"},
            ).stdout
        except CommandError as exc:
            raise CommandError(
                f"Couldn't invoke {function}: {exc} If this stage hasn't been deployed since "
                f"Plan 7a, run just deploy-{stage} first."
            ) from exc
        answer = json.loads(answer_file.read_text(encoding="utf-8"))
    if json.loads(metadata).get("FunctionError"):
        error_type = str(answer.get("errorType", "an unknown error"))
        if error_type in TOLD:
            raise CommandError(str(answer.get("errorMessage", error_type)))
        if error_type == "JobFailed":  # the function's stand-in, whose message is the type
            error_type = str(answer.get("errorMessage", error_type))
        raise CommandError(
            f"The restore drill failed with {error_type}. Its logs are in CloudWatch, in "
            f"/aws/lambda/{function}, and in Grafana (service nettriage-ops)."
        )
    return (
        f"The restore drill restored the backup of {answer['day']}: {answer['tables']} tables "
        f"and {answer['rows']} rows, every table's count matching its manifest."
    )
