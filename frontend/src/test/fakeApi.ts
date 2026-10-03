/**
 * A stand-in for the API in tests: answers `fetch` from a table of routes ("GET /api/v1/me")
 * and keeps every request it was sent. Vitest restores the real `fetch` after each test.
 */
import { vi } from "vitest";

export interface Reply {
  status?: number;
  body?: unknown;
  headers?: Record<string, string>;
}

export type Handler = Reply | ((request: Request) => Reply | Promise<Reply>);

export interface FakeApi {
  /** Every request, in order, as sent (bodies still readable). */
  requests: Request[];
  on(route: string, handler: Handler): void;
}

function respond(reply: Reply): Response {
  const status = reply.status ?? 200;
  const empty = reply.body === undefined || status === 204;
  const type = status >= 400 ? "application/problem+json" : "application/json";
  return new Response(empty ? null : JSON.stringify(reply.body), {
    status,
    headers: { "content-type": type, ...reply.headers },
  });
}

export function fakeApi(routes: Record<string, Handler> = {}): FakeApi {
  const table = new Map(Object.entries(routes));
  const requests: Request[] = [];
  vi.stubGlobal("fetch", async (request: Request) => {
    requests.push(request.clone());
    const route = `${request.method} ${new URL(request.url).pathname}`;
    const handler = table.get(route);
    if (handler === undefined) {
      return respond({ status: 404, body: { title: "Not Found", detail: `No fake for ${route}` } });
    }
    return respond(typeof handler === "function" ? await handler(request) : handler);
  });
  return { requests, on: (route, handler) => void table.set(route, handler) };
}
