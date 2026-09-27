import subprocess
import sys

import pytest

from tools.deploy.runner import CommandError, run


def test_captures_stdout() -> None:
    assert run([sys.executable, "-c", "print('hello')"]).stdout.strip() == "hello"


def test_failure_names_only_the_program_and_subcommand() -> None:
    script = "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"
    with pytest.raises(CommandError) as err:
        run([sys.executable, "-c", script, "super-secret-value"])
    message = str(err.value)
    assert "exit code 3" in message
    assert "boom" in message
    assert "super-secret-value" not in message


def test_unchecked_failure_returns_the_exit_code() -> None:
    assert run([sys.executable, "-c", "import sys; sys.exit(4)"], check=False).returncode == 4


def test_missing_program_is_explained() -> None:
    with pytest.raises(CommandError, match="isn't installed"):
        run(["nettriage-no-such-program"])


def test_redacted_values_never_reach_the_error_message() -> None:
    script = "import sys; sys.stderr.write('Invalid value: tok-123\\n'); sys.exit(2)"
    with pytest.raises(CommandError) as err:
        run([sys.executable, "-c", script], redact=["tok-123"])
    message = str(err.value)
    assert "tok-123" not in message
    assert "***" in message


class _FakeProcess:
    """A Popen stand-in whose first `wait()` is interrupted, like a child getting Ctrl+C."""

    def __init__(self) -> None:
        self.waits = 0
        self.killed = False
        self.terminated = False

    def wait(self) -> int:
        self.waits += 1
        if self.waits == 1:
            raise KeyboardInterrupt
        return 0

    def kill(self) -> None:
        self.killed = True

    def terminate(self) -> None:
        self.terminated = True


def test_interactive_commands_let_a_ctrl_c_child_stop_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)

    result = run(["terraform", "apply"], interactive=True)

    assert result.returncode == 0
    assert process.waits == 2
    assert process.killed is False
    assert process.terminated is False
