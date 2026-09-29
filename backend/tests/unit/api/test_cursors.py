"""Page cursors (spec §7): opaque to clients, exact for the server."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi import HTTPException

from nettriage.entrypoints.api.cursors import decode_cursor, encode_cursor

AT = datetime(2026, 9, 29, 8, 34, 26, 169892, tzinfo=UTC)
ITEM = UUID("01a0ec4d-060a-7266-a427-fce3ccd0d827")


def test_a_cursor_brings_back_the_exact_position() -> None:
    cursor = encode_cursor(AT, ITEM)

    assert decode_cursor(cursor) == (AT, ITEM)
    assert cursor.isascii()
    assert "=" not in cursor


@pytest.mark.parametrize("cursor", ["", "not-base64!", "WzFd", "WyJ4IiwgInkiXQ"])
def test_a_broken_cursor_is_a_422(cursor: str) -> None:
    with pytest.raises(HTTPException) as refused:
        decode_cursor(cursor)

    assert refused.value.status_code == 422
