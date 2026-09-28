from datetime import timedelta

from conftest import FakeClock, RuntimeTable

from nettriage.adapters.login_states import LoginStateStore
from nettriage.application.sign_in import LoginState

LOGIN = LoginState(code_verifier="verifier", nonce="nonce", return_to="/app/orgs")


def test_a_login_state_can_be_taken_once(runtime_table: RuntimeTable, clock: FakeClock) -> None:
    states = LoginStateStore(runtime_table.client, runtime_table.name)
    states.put("state-1", LOGIN, clock())

    assert states.take("state-1", clock()) == LOGIN
    assert states.take("state-1", clock()) is None


def test_an_unknown_state_is_refused(runtime_table: RuntimeTable, clock: FakeClock) -> None:
    states = LoginStateStore(runtime_table.client, runtime_table.name)

    assert states.take("never-issued", clock()) is None


def test_a_state_is_still_good_after_fourteen_minutes(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    """A first sign-up also verifies the email and sets up the authenticator app."""
    states = LoginStateStore(runtime_table.client, runtime_table.name)
    states.put("state-1", LOGIN, clock())
    clock.advance(timedelta(minutes=14))

    assert states.take("state-1", clock()) == LOGIN


def test_a_state_older_than_fifteen_minutes_is_refused_even_before_dynamodb_expires_it(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    states = LoginStateStore(runtime_table.client, runtime_table.name)
    states.put("state-1", LOGIN, clock())
    clock.advance(timedelta(minutes=15))

    assert states.take("state-1", clock()) is None
