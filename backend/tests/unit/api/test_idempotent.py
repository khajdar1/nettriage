"""What an Idempotency-Key's request hash covers (spec §7)."""

from pydantic import BaseModel

from nettriage.entrypoints.api.idempotent import request_hash


class Body(BaseModel):
    name: str
    size: int


def test_the_same_request_hashes_the_same_whatever_its_field_order() -> None:
    first = Body(name="flows.log", size=1)
    again = Body.model_validate({"size": 1, "name": "flows.log"})

    path = "/api/v1/orgs"
    assert request_hash("POST", path, first) == request_hash("POST", path, again)


def test_another_method_path_or_body_is_another_request() -> None:
    body = Body(name="flows.log", size=1)

    hashes = {
        request_hash("POST", "/api/v1/orgs", body),
        request_hash("PUT", "/api/v1/orgs", body),
        request_hash("POST", "/api/v1/orgs/one/uploads", body),
        request_hash("POST", "/api/v1/orgs/two/uploads", body),
        request_hash("POST", "/api/v1/orgs", Body(name="flows.log", size=2)),
    }

    assert len(hashes) == 5
