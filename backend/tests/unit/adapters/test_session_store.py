from datetime import timedelta
from typing import Any
from uuid import uuid4

from conftest import CountingClient, FakeClock, RuntimeTable

from nettriage.adapters.sessions import SessionStore
from nettriage.application.sessions import session_key


def store(table: RuntimeTable) -> SessionStore:
    return SessionStore(table.client, table.name)


def test_a_created_session_is_found_by_its_id(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    sessions = store(runtime_table)
    user = uuid4()

    session_id, created = sessions.create(
        user_id=user, now=clock(), ip="203.0.113.7", user_agent="Firefox"
    )

    assert sessions.get(session_id) == created
    assert (created.user_id, created.created_at, created.last_seen_at) == (user, clock(), clock())


def test_only_the_hash_of_the_session_id_is_stored(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    session_id, _ = store(runtime_table).create(
        user_id=uuid4(), now=clock(), ip=None, user_agent=None
    )

    items = runtime_table.client.scan(TableName=runtime_table.name)["Items"]
    assert session_id not in str(items)
    assert any(item["pk"]["S"] == f"SESSION#{session_key(session_id)}" for item in items)


def test_an_unknown_session_id_finds_nothing(runtime_table: RuntimeTable) -> None:
    assert store(runtime_table).get("not-a-session") is None


def test_touching_records_activity_and_pushes_the_ttl(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    sessions = store(runtime_table)
    session_id, session = sessions.create(user_id=uuid4(), now=clock(), ip=None, user_agent=None)
    clock.advance(timedelta(minutes=10))

    touched = sessions.touch(session, clock())

    assert touched is not None
    assert sessions.get(session_id) == touched
    assert touched.last_seen_at == clock()


def test_touching_a_deleted_session_reports_it_gone(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    sessions = store(runtime_table)
    _, session = sessions.create(user_id=uuid4(), now=clock(), ip=None, user_agent=None)
    sessions.delete(session)

    assert sessions.touch(session, clock()) is None


def test_signing_out_deletes_only_that_session(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    sessions = store(runtime_table)
    user = uuid4()
    first_id, first = sessions.create(user_id=user, now=clock(), ip=None, user_agent=None)
    second_id, _ = sessions.create(user_id=user, now=clock(), ip=None, user_agent=None)

    sessions.delete(first)

    assert sessions.get(first_id) is None
    assert sessions.get(second_id) is not None


def test_signing_out_everywhere_deletes_every_session_of_that_user_only(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    sessions = store(runtime_table)
    user, other = uuid4(), uuid4()
    mine = [
        sessions.create(user_id=user, now=clock(), ip=None, user_agent=None)[0] for _ in range(30)
    ]
    theirs, _ = sessions.create(user_id=other, now=clock(), ip=None, user_agent=None)

    deleted = sessions.delete_all(user)

    assert deleted == 30
    assert all(sessions.get(session_id) is None for session_id in mine)
    assert sessions.get(theirs) is not None
    assert sessions.delete_all(user) == 0


def test_signing_out_after_signing_out_everywhere_leaves_nothing_that_never_expires(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    sessions = store(runtime_table)
    user = uuid4()
    _, session = sessions.create(user_id=user, now=clock(), ip=None, user_agent=None)
    sessions.delete_all(user)

    sessions.delete(session)

    items = runtime_table.client.scan(TableName=runtime_table.name)["Items"]
    assert all("expires_at" in item for item in items)


def test_a_session_created_while_signing_out_everywhere_stays_listed(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    """A sign-in that lands between reading the user's sessions and deleting them must survive,
    and still be found by the next "sign out everywhere"."""
    user = uuid4()
    late: list[str] = []
    other = SessionStore(runtime_table.client, runtime_table.name)

    class Racing(CountingClient):
        def batch_write_item(self, **kwargs: Any) -> Any:
            if not late:
                late.append(other.create(user_id=user, now=clock(), ip=None, user_agent=None)[0])
            return runtime_table.client.batch_write_item(**kwargs)

    sessions = SessionStore(Racing(runtime_table.client), runtime_table.name)  # type: ignore[arg-type]
    sessions.create(user_id=user, now=clock(), ip=None, user_agent=None)

    sessions.delete_all(user)

    assert sessions.get(late[0]) is not None
    assert sessions.delete_all(user) == 1
    assert sessions.get(late[0]) is None
