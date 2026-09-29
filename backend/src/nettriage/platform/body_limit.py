"""Request bodies are at most 64 KB (spec §6.7): the API takes small JSON, and files go
straight to S3. A declared Content-Length over the limit is refused before anything else runs;
a body that arrives without one is counted as it's read."""

from __future__ import annotations

from http import HTTPStatus

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nettriage.platform.errors import problem_response

MAX_BODY_BYTES = 64 * 1024
DETAIL = f"Request bodies are limited to {MAX_BODY_BYTES // 1024} KB."


class BodyTooLarge(HTTPException):
    """Raised while a route reads its body. It's an HTTPException, so FastAPI passes it on to
    the Problem Details handler instead of turning it into "error parsing the body"."""

    def __init__(self) -> None:
        super().__init__(HTTPStatus.CONTENT_TOO_LARGE, detail=DETAIL)


class BodySizeLimit:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.max_bytes):
            response = problem_response(Request(scope), HTTPStatus.CONTENT_TOO_LARGE, DETAIL)
            await response(scope, receive, send)
            return
        received = 0

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLarge
            return message

        await self.app(scope, counted, send)
