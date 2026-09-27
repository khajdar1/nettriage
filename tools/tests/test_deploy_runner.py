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
