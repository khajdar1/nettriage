import subprocess
import sys

import pytest

from tools.deploy.runner import CommandError, run


def test_captures_stdout() -> None:
    assert run([sys.executable, "-c", "print('hello')"]).stdout.strip() == "hello"


def test_non_utf8_stdout_does_not_raise_unicodedecodeerror() -> None:
    """Windows tools (e.g. a legacy-codepage error) can emit bytes that aren't valid UTF-8;
    the runner must replace them instead of raising UnicodeDecodeError."""
    script = "import sys; sys.stdout.buffer.write(b'before \\xff\\xfe after'); sys.exit(0)"
    result = run([sys.executable, "-c", script])
    assert result.returncode == 0
    assert "before" in result.stdout and "after" in result.stdout


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


def test_terraform_style_stderr_becomes_a_readable_message() -> None:
    """Terraform's own stderr is ANSI-colored box-drawing art; the error should read cleanly.

    The child writes raw UTF-8 bytes through sys.stderr.buffer (bypassing its own text-mode
    console encoding, which on Windows may not represent these characters) so the parent's
    utf-8 decode of the captured stderr sees exactly the bytes Terraform itself would emit.
    """
    script = (
        "import sys\n"
        "sys.stderr.buffer.write('\\x1b[31m\\u2577\\x1b[0m\\n'.encode('utf-8'))\n"
        "sys.stderr.buffer.write('\\u2502 \\x1b[1mError: \\x1b[0mInvalid backend\\n'.encode('utf-8'))\n"
        "sys.stderr.buffer.write('\\u2502 \\n'.encode('utf-8'))\n"
        "sys.stderr.buffer.write('\\u2575\\n'.encode('utf-8'))\n"
        "sys.exit(1)\n"
    )
    with pytest.raises(CommandError) as err:
        run([sys.executable, "-c", script])
    message = str(err.value)
    assert "Error: Invalid backend" in message
    assert "\u2575" not in message
    assert "\u2502" not in message
