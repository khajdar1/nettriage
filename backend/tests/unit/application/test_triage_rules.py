"""Triage rules (spec §7): which `If-Match` headers name a version, and who can be assigned."""

import pytest

from nettriage.application.triage import can_be_assigned, etag, expected_version


def test_an_etag_is_the_version_in_quotes() -> None:
    assert etag(3) == '"3"'
    assert expected_version(etag(3)) == 3
    assert expected_version(' "12" ') == 12


@pytest.mark.parametrize(
    "header",
    [None, "", "*", "3", 'W/"3"', '"03"', '"0"', '"3", "4"', '"three"', '"12345678901"'],
)
def test_anything_but_one_version_etag_names_no_version(header: str | None) -> None:
    assert expected_version(header) is None


@pytest.mark.parametrize(
    ("role", "assignable"),
    [("owner", True), ("admin", True), ("analyst", True), ("viewer", False), (None, False)],
)
def test_only_a_member_who_can_triage_can_be_assigned(role: str | None, assignable: bool) -> None:
    assert can_be_assigned(role) is assignable  # type: ignore[arg-type]
