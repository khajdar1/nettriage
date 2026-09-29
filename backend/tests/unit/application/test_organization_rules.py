import hashlib
import re

import pytest

from nettriage.application.organizations import (
    MAX_SLUG,
    invitation_url,
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
    ],
)
def test_emails_are_trimmed_and_checked_for_shape(value: str, email: str | None) -> None:
    assert normalize_email(value) == email
