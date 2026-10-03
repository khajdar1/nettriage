import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { ApiError } from "../api/problem";
import { ME_KEY } from "../auth/session";

/**
 * Server state for the app (TanStack Query). A failed read is retried only when a retry can
 * help: a network error or a 5xx, never a 4xx. A 401 from a read or a write means the session
 * ended, so the session is checked again and the person is asked to sign in.
 */
export function createQueryClient({ retries = 2 }: { retries?: number } = {}): QueryClient {
  const sessionEnded = (error: Error) => error instanceof ApiError && error.status === 401;
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError(error, query) {
        if (sessionEnded(error) && query.queryKey[0] !== ME_KEY[0]) {
          void client.invalidateQueries({ queryKey: ME_KEY });
        }
      },
    }),
    mutationCache: new MutationCache({
      onError(error) {
        if (sessionEnded(error)) {
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
