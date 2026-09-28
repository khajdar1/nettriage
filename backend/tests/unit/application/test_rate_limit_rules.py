from datetime import timedelta

import pytest

from nettriage.application.rate_limits import (
    POLICIES,
    Policy,
    allowed,
    failed_open,
    header_values,
    ip_subject,
    limited,
    viewer_ip,
)

MINUTE = Policy("test", 60, timedelta(minutes=1), 3)  # T = 1 s, tau = 2 s


def test_the_spec_policies_have_whole_millisecond_intervals() -> None:
    assert set(POLICIES) == {
        "api.user",
        "api.mutation.user",
        "auth.ip",
        "public.ip",
        "uploads.org",
        "invites.org",
        "ai.rerun.user",
    }
    for policy in POLICIES.values():
        assert policy.interval_ms * policy.limit == policy.period_ms, policy.name


def test_a_fresh_key_leaves_the_rest_of_the_burst() -> None:
    decision = allowed(MINUTE, tat_ms=1_000, now_ms=0)

    assert (decision.allowed, decision.remaining, decision.reset_seconds) == (True, 2, 1)


def test_the_last_slot_of_the_burst_leaves_nothing() -> None:
    assert allowed(MINUTE, tat_ms=3_000, now_ms=0).remaining == 0


def test_a_limited_request_is_told_when_to_retry() -> None:
    decision = limited(MINUTE, tat_ms=3_000, now_ms=0)

    assert (decision.allowed, decision.retry_after_seconds, decision.reset_seconds) == (
        False,
        1,
        3,
    )


def test_headers_list_every_policy_checked() -> None:
    user = POLICIES["api.user"]
    mutation = POLICIES["api.mutation.user"]

    headers = header_values([allowed(user, 500, 0), allowed(mutation, 2_000, 0)])

    assert headers == {
        "RateLimit-Policy": '"api.user";q=120;w=60, "api.mutation.user";q=30;w=60',
        "RateLimit": '"api.user";r=29;t=1, "api.mutation.user";r=9;t=2',
    }


def test_a_fail_open_decision_reports_no_headers() -> None:
    assert header_values([failed_open(MINUTE)]) == {}


@pytest.mark.parametrize(
    ("header", "subject"),
    [
        ("198.51.100.10:46532", "198.51.100.10"),
        ("2001:db8:1:2:3:4:5:6:46532", "2001:db8:1:2::/64"),
        ("[2001:db8:1:2::7]:443", "2001:db8:1:2::/64"),
        (None, None),
        ("", None),
        ("not-an-address:1", None),
        ("198.51.100.10", None),
    ],
)
def test_the_ip_subject_comes_from_cloudfronts_viewer_address(
    header: str | None, subject: str | None
) -> None:
    assert ip_subject(header) == subject


def test_the_viewer_ip_keeps_the_whole_address() -> None:
    assert viewer_ip("2001:db8:1:2:3:4:5:6:46532") == "2001:db8:1:2:3:4:5:6"
    assert viewer_ip("198.51.100.10:46532") == "198.51.100.10"
