import base64
import hashlib
from uuid import UUID

from nettriage.application.uploads import (
    MAX_UPLOAD_BYTES,
    PRESIGNED_PUT_LIFETIME,
    checksum_header,
    s3_key,
)

ORG = UUID("01a0ec4d-060a-7266-a427-fce3ccd0d827")
UPLOAD = UUID("01a0ec4d-374f-740e-85e7-4ac6ee451cd5")


def test_the_limits_are_the_specs() -> None:
    assert MAX_UPLOAD_BYTES == 26_214_400
    assert PRESIGNED_PUT_LIFETIME.total_seconds() == 300


def test_an_objects_key_is_built_from_ids_only() -> None:
    assert s3_key(ORG, UPLOAD) == f"orgs/{ORG}/uploads/{UPLOAD}/raw"


def test_the_checksum_header_is_the_digest_in_base64() -> None:
    digest = hashlib.sha256(b"flows").digest()

    assert checksum_header(digest.hex()) == base64.b64encode(digest).decode()
