/**
 * API errors (spec §7, §10): RFC 9457 Problem Details become an `ApiError`, shown as a message
 * that includes the error's reference. A 429 says when to try again.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string | null;
  readonly reference: string | null;
  readonly retryAfterSeconds: number | null;

  constructor(
    status: number,
    title: string,
    detail: string | null,
    reference: string | null,
    retryAfterSeconds: number | null,
  ) {
    super(detail ?? title);
    this.name = "ApiError";
    this.status = status;
    this.title = title;
    this.detail = detail;
    this.reference = reference;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

/** The error a response describes, from its Problem Details body and `Retry-After`. */
export function apiError(response: Response, body: unknown): ApiError {
  const problem: Record<string, unknown> =
    typeof body === "object" && body !== null ? { ...body } : {};
  const retryAfter = Number(response.headers.get("Retry-After"));
  return new ApiError(
    response.status,
    text(problem.title) ?? text(response.statusText) ?? `Request failed (${response.status})`,
    text(problem.detail),
    text(problem.trace_id),
    Number.isFinite(retryAfter) && retryAfter > 0 ? Math.ceil(retryAfter) : null,
  );
}

/** openapi-fetch's result as its data, or a thrown `ApiError`. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (!result.response.ok) {
    throw apiError(result.response, result.error);
  }
  return result.data as T;
}

function wait(seconds: number): string {
  if (seconds < 60) {
    return seconds === 1 ? "1 second" : `${seconds} seconds`;
  }
  const minutes = Math.ceil(seconds / 60);
  return minutes === 1 ? "1 minute" : `${minutes} minutes`;
}

/** What the person reads: the problem, its reference for support, or when to try again. */
export function errorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Something went wrong. Check your connection and try again.";
  }
  if (error.status === 429) {
    return error.retryAfterSeconds === null
      ? "Too many requests. Try again shortly."
      : `Too many requests. Try again in ${wait(error.retryAfterSeconds)}.`;
  }
  const message = error.detail ?? error.title;
  return error.reference === null ? message : `${message} (reference ${error.reference})`;
}
