"""An organization's overview (Plan 6d), as the API returns it."""

from __future__ import annotations

from pydantic import BaseModel

from nettriage.adapters.overview import Overview
from nettriage.entrypoints.api.upload_schemas import UploadOut


class SeverityCounts(BaseModel):
    critical: int
    high: int
    medium: int
    low: int


class StatusCounts(BaseModel):
    open: int
    investigating: int
    resolved: int
    false_positive: int


class OverviewOut(BaseModel):
    """Unresolved means Open or Investigating; `new_last_day` counts the findings detected in the
    last 24 hours, whatever their status."""

    unresolved_by_severity: SeverityCounts
    by_status: StatusCounts
    unresolved_unassigned: int
    unresolved_mine: int
    new_last_day: int
    member_count: int
    last_upload: UploadOut | None

    @classmethod
    def of(cls, overview: Overview) -> OverviewOut:
        return cls(
            unresolved_by_severity=SeverityCounts(**overview.unresolved_by_severity),
            by_status=StatusCounts(**overview.by_status),
            unresolved_unassigned=overview.unresolved_unassigned,
            unresolved_mine=overview.unresolved_mine,
            new_last_day=overview.new_last_day,
            member_count=overview.member_count,
            last_upload=None
            if overview.last_upload is None
            else UploadOut.of(overview.last_upload),
        )
