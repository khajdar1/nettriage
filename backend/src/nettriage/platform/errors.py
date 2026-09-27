"""RFC 9457 Problem Details for every error the API returns."""

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from nettriage.platform.trace_context import current_trace_id

PROBLEM_JSON = "application/problem+json"

logger = logging.getLogger(__name__)


class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    trace_id: str | None = None


def problem_response(
    request: Request,
    status: int,
    detail: str | None = None,
    extensions: Mapping[str, Any] | None = None,
) -> JSONResponse:
    problem = Problem(
        title=HTTPStatus(status).phrase,
        status=status,
        detail=detail,
        instance=request.url.path,
        trace_id=current_trace_id(),
    )
    body: dict[str, Any] = problem.model_dump()
    if extensions:
        body.update(extensions)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


async def _http_exception_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, StarletteHTTPException):
        raise TypeError(type(exc))
    phrase = HTTPStatus(exc.status_code).phrase
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail != phrase else None
    response = problem_response(request, exc.status_code, detail)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _validation_error_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, RequestValidationError):
        raise TypeError(type(exc))
    # Never `input` or `ctx`: FastAPI's default body echoes the submitted value, which can hold
    # a password, token or other secret the caller typed into a query, path or body field.
    errors = [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return problem_response(request, HTTPStatus.UNPROCESSABLE_ENTITY, extensions={"errors": errors})


async def _unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    logger.exception("unhandled_error", extra={"route": request.url.path})
    return problem_response(request, HTTPStatus.INTERNAL_SERVER_ERROR)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
