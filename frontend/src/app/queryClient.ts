import { QueryCache, QueryClient } from "@tanstack/react-query";
import { ApiError } from "../api/problem";
import { ME_KEY } from "../auth/session";

/**
 * Server state for the app (TanStack Query). A failed read is retried only when a retry can
 * help: a network error or a 5xx, never a 4xx. A 401 means the session ended, so the person
 * is asked to sign in again.
 */
export function createQueryClient({ retries = 2 }: { retries?: number } = {}): QueryClient {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError(error, query) {
        if (error instanceof ApiError && error.status === 401 && query.queryKey[0] !== ME_KEY[0]) {
          void client.invalidateQueries({ queryKey: ME_KEY });
        }
      },
    }),
    defaultOptions: {
      queries: {
        retry: (failures, error) =>
          failures < retries && !(error instanceof ApiError && error.status < 500),
      },
      mutations: { retry: false },
    },
  });
  return client;
}
