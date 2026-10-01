"""Request bodies for triage (spec §7). Like every request model, they forbid fields they don't
declare, so a triage change can't touch a finding's severity, title or anything else."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import StringConstraints, model_validator

from nettriage.adapters.findings import FindingStatus
from nettriage.adapters.triage import Triage
from nettriage.application.triage import MAX_COMMENT
from nettriage.entrypoints.api.schemas import Strict

# 1 to 2,000 characters after trimming (spec §5.2). Line breaks and tabs are fine in a comment;
# other control characters aren't.
CommentText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_COMMENT,
        pattern=r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+$",
    ),
]


class TriageIn(Strict):
    """A status, an assignee, or both. `"assignee_id": null` unassigns; leaving a field out
    leaves it as it is."""

    status: FindingStatus | None = None
    assignee_id: UUID | None = None

    @model_validator(mode="after")
    def says_what_to_change(self) -> TriageIn:
        if not self.model_fields_set:
            raise ValueError("Send a status, an assignee_id, or both.")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status can't be null.")
        return self

    def change(self) -> Triage:
        return Triage(
            status=self.status,
            assign="assignee_id" in self.model_fields_set,
            assignee_id=self.assignee_id,
        )


class CommentIn(Strict):
    text: CommentText
