from datetime import timedelta

from conftest import CountingClient, FakeClock, RuntimeTable

from nettriage.adapters.rate_limiter import RateLimiter
from nettriage.application.rate_limits import Policy

POLICY = Policy("test", 60, timedelta(minutes=1), 3)  # T = 1 s, burst 3


def limiter(table: RuntimeTable, clock: FakeClock) -> RateLimiter:
    return RateLimiter(table.client, table.name, clock)


def test_a_burst_is_allowed_then_the_next_request_is_limited(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    limits = limiter(runtime_table, clock)

    decisions = [limits.check(POLICY, "user-1") for _ in range(4)]

    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert [d.remaining for d in decisions[:3]] == [2, 1, 0]
    assert decisions[3].retry_after_seconds == 1


def test_one_request_is_allowed_again_after_one_interval(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    limits = limiter(runtime_table, clock)
    for _ in range(3):
        limits.check(POLICY, "user-1")

    clock.advance(timedelta(seconds=1))

    assert limits.check(POLICY, "user-1").allowed
    assert not limits.check(POLICY, "user-1").allowed


def test_an_idle_key_gets_its_whole_burst_back(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    limits = limiter(runtime_table, clock)
    for _ in range(4):
        limits.check(POLICY, "user-1")

    clock.advance(timedelta(minutes=5))

    assert [limits.check(POLICY, "user-1").allowed for _ in range(4)] == [True, True, True, False]


def test_subjects_and_policies_are_counted_separately(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    limits = limiter(runtime_table, clock)
    other_policy = Policy("other", 60, timedelta(minutes=1), 3)
    for _ in range(3):
        limits.check(POLICY, "user-1")

    assert limits.check(POLICY, "user-2").allowed
    assert limits.check(other_policy, "user-1").allowed


def test_a_dynamodb_failure_lets_the_request_through_marked_degraded(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    broken = RateLimiter(runtime_table.client, "no-such-table", clock)

    decision = broken.check(POLICY, "user-1")

    assert (decision.allowed, decision.degraded) == (True, True)


def test_limits_are_audited_at_most_once_per_minute_per_subject(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    limits = limiter(runtime_table, clock)

    first = limits.should_audit(POLICY, "user-1")
    again = limits.should_audit(POLICY, "user-1")
    someone_else = limits.should_audit(POLICY, "user-2")
    clock.advance(timedelta(minutes=1))
    later = limits.should_audit(POLICY, "user-1")

    assert (first, again, someone_else, later) == (True, False, True, True)


def counting(table: RuntimeTable, clock: FakeClock) -> tuple[RateLimiter, CountingClient]:
    client = CountingClient(table.client)
    return RateLimiter(client, table.name, clock), client  # type: ignore[arg-type]


def test_an_active_key_costs_one_write_per_allowed_request(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    """The provisioned table has only 10 write units a second: a failed conditional update
    costs a unit too, so the limiter starts with the update most likely to succeed."""
    limits, client = counting(runtime_table, clock)

    assert all(limits.check(POLICY, "user-1").allowed for _ in range(3))

    assert client.calls["update_item"] == 3


def test_a_limited_subject_is_refused_without_asking_dynamodb_until_it_may_retry(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    """A flood from one subject must not spend the table's capacity: tat never decreases, so a
    subject this process has seen over its limit stays over it until tat - tau."""
    limits, client = counting(runtime_table, clock)
    for _ in range(3):
        limits.check(POLICY, "user-1")
    writes = client.calls["update_item"]

    refused = [limits.check(POLICY, "user-1") for _ in range(50)]
    clock.advance(timedelta(seconds=1))
    again = limits.check(POLICY, "user-1")

    assert not any(decision.allowed for decision in refused)
    assert {decision.retry_after_seconds for decision in refused} == {1}
    assert client.calls["update_item"] == writes + 1
    assert again.allowed


def test_limits_are_sampled_for_the_audit_log_without_extra_writes(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    limits, client = counting(runtime_table, clock)

    sampled = [limits.should_audit(POLICY, "user-1") for _ in range(20)]

    assert sampled.count(True) == 1
    assert client.calls["put_item"] == 1
