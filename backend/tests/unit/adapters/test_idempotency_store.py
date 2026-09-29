from datetime import timedelta
from uuid import uuid4

import pytest
from conftest import FakeClock, RuntimeTable

from nettriage.adapters.idempotency import (
    IdempotencyInProgress,
    IdempotencyMismatch,
    IdempotencyStore,
    StoredResponse,
)

DONE = StoredResponse(status=201, body={"id": "org-1", "name": "Acme"})


def test_a_new_key_lets_the_request_run(runtime_table: RuntimeTable, clock: FakeClock) -> None:
    store = IdempotencyStore(runtime_table.client, runtime_table.name)

    assert store.begin(uuid4(), "key-1", "hash-a", clock()) is None


def test_a_finished_request_is_answered_again_from_the_store(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    store = IdempotencyStore(runtime_table.client, runtime_table.name)
    user = uuid4()
    store.begin(user, "key-1", "hash-a", clock())
    store.finish(user, "key-1", DONE, clock())

    assert store.begin(user, "key-1", "hash-a", clock()) == DONE


def test_the_same_key_with_another_request_is_refused(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    store = IdempotencyStore(runtime_table.client, runtime_table.name)
    user = uuid4()
    store.begin(user, "key-1", "hash-a", clock())
    store.finish(user, "key-1", DONE, clock())

    with pytest.raises(IdempotencyMismatch):
        store.begin(user, "key-1", "hash-b", clock())


def test_a_retry_while_the_first_request_runs_is_refused(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    store = IdempotencyStore(runtime_table.client, runtime_table.name)
    user = uuid4()
    store.begin(user, "key-1", "hash-a", clock())

    with pytest.raises(IdempotencyInProgress):
        store.begin(user, "key-1", "hash-a", clock())


def test_keys_belong_to_one_user_abandoned_ones_can_be_retried_and_they_last_a_day(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    store = IdempotencyStore(runtime_table.client, runtime_table.name)
    user = uuid4()
    store.begin(user, "key-1", "hash-a", clock())

    assert store.begin(uuid4(), "key-1", "hash-a", clock()) is None  # another user's key
    store.abandon(user, "key-1")
    assert store.begin(user, "key-1", "hash-b", clock()) is None
    store.finish(user, "key-1", DONE, clock())
    clock.advance(timedelta(hours=24))
    assert store.begin(user, "key-1", "hash-c", clock()) is None


def test_a_key_left_unfinished_frees_itself_after_a_minute(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    """If the work succeeded but storing its response failed, a retry mustn't be refused as
    "still running" for a whole day."""
    store = IdempotencyStore(runtime_table.client, runtime_table.name)
    user = uuid4()
    store.begin(user, "key-1", "hash-a", clock())
    clock.advance(timedelta(minutes=1))

    assert store.begin(user, "key-1", "hash-a", clock()) is None
