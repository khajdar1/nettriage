"""The OpenAPI document the frontend's typed client is generated from (spec §7, §11.4)."""

from pathlib import Path

from nettriage.entrypoints.api.openapi_document import main, openapi_document, render

COMMITTED = Path(__file__).resolve().parents[3] / "frontend" / "openapi.json"


def test_the_committed_document_matches_the_api() -> None:
    assert COMMITTED.read_text(encoding="utf-8") == render(openapi_document()), (
        "The API changed: run `just openapi`, then commit frontend/openapi.json and "
        "frontend/src/api/schema.ts."
    )


def test_the_document_names_the_apis_version_not_a_builds() -> None:
    assert openapi_document()["info"]["version"] == "1"


def test_every_route_is_under_api() -> None:
    paths = list(openapi_document()["paths"])

    assert paths
    assert all(path.startswith("/api/") for path in paths)


def test_main_writes_the_document(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"

    main(["openapi_document", str(out)])

    assert out.read_text(encoding="utf-8") == render(openapi_document())
