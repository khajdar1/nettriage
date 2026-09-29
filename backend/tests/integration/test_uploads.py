"""Uploads in Postgres (spec §5.2, §5.3): created as `pending_upload` by a member whose role may
upload, read only within their org."""

from uuid import UUID, uuid4, uuid7

import pytest
from conftest import Database
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from tenantdata import add_member, add_tenant, add_upload, add_user

from nettriage.adapters.uploads import Upload, create_upload, get_upload, list_uploads
from nettriage.application.organizations import Forbidden, NotFound
from nettriage.application.uploads import UPLOAD_STATUSES, s3_key

SHA256 = "ab" * 32


def upload_as(database: Database, org: UUID, user: UUID, filename: str = "flows.log") -> Upload:
    upload_id = uuid7()
    return create_upload(
        database.app_api,
        org,
        user_id=user,
        upload_id=upload_id,
        filename=filename,
        size_bytes=2048,
        sha256=SHA256,
        s3_key=s3_key(org, upload_id),
    )


def test_an_upload_starts_pending_with_what_the_member_declared(database: Database) -> None:
    tenant = add_tenant(database.admin)

    upload = upload_as(database, tenant.org_id, tenant.owner_id, "vpc flows.log.gz")

    assert (upload.org_id, upload.uploaded_by, upload.original_filename) == (
        tenant.org_id,
        tenant.owner_id,
        "vpc flows.log.gz",
    )
    assert (upload.size_bytes, upload.sha256, upload.status) == (2048, SHA256, "pending_upload")
    assert (upload.rows_parsed, upload.rejected_samples, upload.findings_truncated) == (
        None,
        [],
        0,
    )
    assert get_upload(database.app_api, tenant.org_id, tenant.owner_id, upload.id) == upload


def test_a_viewer_can_not_upload(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        viewer = add_user(connection)
        add_member(connection, tenant.org_id, viewer, "viewer")

    with pytest.raises(Forbidden):
        upload_as(database, tenant.org_id, viewer)


def test_someone_removed_from_the_org_can_not_upload(database: Database) -> None:
    tenant = add_tenant(database.admin)
    stranger = uuid4()

    with pytest.raises(NotFound):
        upload_as(database, tenant.org_id, stranger)


def test_uploads_are_listed_newest_first_page_by_page(database: Database) -> None:
    tenant = add_tenant(database.admin)
    created = [upload_as(database, tenant.org_id, tenant.owner_id) for _ in range(3)]
    newest_first = [tenant.upload_id, *[upload.id for upload in created]][::-1]

    first = list_uploads(database.app_api, tenant.org_id, tenant.owner_id, limit=2)
    rest = list_uploads(
        database.app_api,
        tenant.org_id,
        tenant.owner_id,
        limit=2,
        before=(first[-1].created_at, first[-1].id),
    )

    assert [upload.id for upload in first + rest] == newest_first


def test_another_orgs_upload_is_not_found(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with pytest.raises(NotFound):
        get_upload(database.app_api, mine.org_id, mine.owner_id, theirs.upload_id)


@pytest.mark.parametrize("status", [*UPLOAD_STATUSES, "done"])
def test_the_database_accepts_exactly_the_statuses_the_code_knows(
    database: Database, status: str
) -> None:
    tenant = add_tenant(database.admin)

    def add() -> None:
        with database.admin.begin() as connection:
            add_upload(connection, tenant.org_id, tenant.owner_id, status)

    if status in UPLOAD_STATUSES:
        add()
    else:
        with pytest.raises(IntegrityError, match="uploads_status_check"):
            add()


def test_an_uploads_checksum_must_be_lowercase_hex(database: Database) -> None:
    tenant = add_tenant(database.admin)

    with pytest.raises(IntegrityError, match="uploads_sha256_check"), database.admin.begin() as c:
        c.execute(
            text("UPDATE uploads SET sha256 = :bad WHERE id = :id"),
            {"bad": "AB" * 32, "id": tenant.upload_id},
        )
