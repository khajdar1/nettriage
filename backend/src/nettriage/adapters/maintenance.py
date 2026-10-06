"""The daily cleanup (Plan 7a §2.4), as `app_ops`. Each rule runs in its own transaction and
repeats its row policy's predicate (migration 0011), so it changes exactly the rows the policy
allows; it returns how many, never which."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, text

from nettriage.application.uploads import GAVE_UP

# An upload's file arrives within its signed URL's 5 minutes, and its analysis message is
# delivered at most three times, 30 minutes apart: after 2 hours, every delivery is over.
STALE = "created_at < now() - interval '2 hours'"


@dataclass(frozen=True)
class CleanupCounts:
    uploads_expired: int
    analyses_failed: int
    invitations_deleted: int
    audit_rows_deleted: int


def _changed(engine: Engine, statement: str, params: dict[str, object] | None = None) -> int:
    with engine.begin() as connection:
        return connection.execute(text(statement), params or {}).rowcount


def expire_abandoned_uploads(engine: Engine) -> int:
    """Uploads whose file never came."""
    return _changed(
        engine,
        f"UPDATE uploads SET status = 'expired' WHERE status = 'pending_upload' AND {STALE}",  # noqa: S608
    )


def fail_stuck_analyses(engine: Engine) -> int:
    """Uploads whose analysis crashed or timed out on its last delivery."""
    return _changed(
        engine,
        "UPDATE uploads SET status = 'failed', failure_reason = :reason, processed_at = now() "  # noqa: S608
        f"WHERE status = 'processing' AND {STALE}",
        {"reason": GAVE_UP},
    )


def delete_lapsed_invitations(engine: Engine) -> int:
    """Invitations nobody accepted, past their date; accepted ones stay as history."""
    return _changed(
        engine, "DELETE FROM invitations WHERE accepted_at IS NULL AND expires_at < now()"
    )


def delete_old_audit_rows(engine: Engine) -> int:
    """The audit log keeps 180 days (spec §5.7)."""
    return _changed(engine, "DELETE FROM audit_log WHERE created_at < now() - interval '180 days'")


def clean_up(engine: Engine) -> CleanupCounts:
    return CleanupCounts(
        uploads_expired=expire_abandoned_uploads(engine),
        analyses_failed=fail_stuck_analyses(engine),
        invitations_deleted=delete_lapsed_invitations(engine),
        audit_rows_deleted=delete_old_audit_rows(engine),
    )
