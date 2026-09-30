import hashlib
import re
import time

import pytest

from nettriage.application.organizations import (
    MAX_SLUG,
    invitation_url,
    looks_like_email,
    new_invitation_token,
    normalize_email,
    slugify,
    token_hash,
    with_suffix,
)

SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")  # the database's CHECK


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Acme Security", "acme-security"),
        ("  Blue   Team!! ", "blue-team"),
        ("Café Zürich", "cafe-zurich"),
        ("SOC-2 / Ops", "soc-2-ops"),
        ("!!!", "org"),
        ("日本", "org"),
    ],
)
def test_slugs_are_lowercase_ascii_words_joined_by_dashes(name: str, slug: str) -> None:
    assert slugify(name) == slug


def test_a_long_name_leaves_room_for_a_suffix_and_the_result_fits_the_database() -> None:
    slug = slugify("word " * 40)
    suffixed = with_suffix(slug)

    assert len(slug) <= 50
    assert len(suffixed) <= MAX_SLUG
    assert SLUG.match(slug)
    assert SLUG.match(suffixed)
    assert suffixed != with_suffix(slug)


def test_invitation_tokens_are_random_hashed_and_travel_in_the_fragment() -> None:
    token = new_invitation_token()

    assert len(token) == 43
    assert token != new_invitation_token()
    assert token_hash(token) == hashlib.sha256(token.encode()).hexdigest()
    assert invitation_url("https://app.test", token) == f"https://app.test/invite#{token}"


@pytest.mark.parametrize(
    ("value", "email"),
    [
        (" Ada@Example.com ", "Ada@Example.com"),
        ("a@b.co", "a@b.co"),
        ("no-at-sign", None),
        ("two@@example.com", None),
        ("spaces in@example.com", None),
        ("nodot@example", None),
        ("x" * 310 + "@example.com", None),
        ("a@b.c.", "a@b.c."),
        ("a@.b.", None),
        ("a@b.", None),
        ("a@.b", None),
        ("@example.com", None),
        ("a@b@c.com", None),
        ("tab\t@example.com", None),
        ("a@exa\u2003mple.com", None),
        ("a!#$%@example.com", "a!#$%@example.com"),
    ],
)
def test_emails_are_trimmed_and_checked_for_shape(value: str, email: str | None) -> None:
    assert normalize_email(value) == email


def test_checking_an_email_takes_linear_time_on_hostile_input() -> None:
    """CodeQL py/polynomial-redos: `[^@\\s]+\\.[^@\\s]+` backtracks quadratically over a long run
    of dots (20,000 took seconds). The length cap kept it small, but the check itself must be
    linear."""
    hostile = "a@" + "." * 20_000 + "@"

    started = time.perf_counter()
    assert looks_like_email(hostile) is False
    assert time.perf_counter() - started < 0.1
