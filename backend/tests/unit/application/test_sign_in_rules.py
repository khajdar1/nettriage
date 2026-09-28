import base64
import hashlib

import pytest

from nettriage.application.sign_in import code_challenge, new_code_verifier, safe_return_to


@pytest.mark.parametrize("path", ["/app", "/app/orgs/acme/findings?status=open", "/invite"])
def test_a_path_inside_the_app_is_kept(path: str) -> None:
    assert safe_return_to(path) == path


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "https://evil.example/",
        "//evil.example/",
        "/\\evil.example/",
        "/app\\..\\evil",
        "javascript:alert(1)",
        "app",
        "/app\r\nSet-Cookie: x=y",
        "/" + "a" * 512,
    ],
)
def test_anything_else_returns_to_the_app(value: str | None) -> None:
    assert safe_return_to(value) == "/app"


def test_the_code_verifier_fits_rfc_7636() -> None:
    verifier = new_code_verifier()

    assert 43 <= len(verifier) <= 128
    assert verifier != new_code_verifier()


def test_the_code_challenge_is_the_unpadded_base64url_sha256_of_the_verifier() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"  # RFC 7636, appendix B

    assert code_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert code_challenge(verifier) == expected.rstrip(b"=").decode()
