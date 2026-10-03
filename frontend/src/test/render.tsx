import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { createQueryClient } from "../app/queryClient";
import { routes } from "../app/routes";

/** The whole app opened at `path`, as a person would see it; failed reads aren't retried. */
export function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const client = createQueryClient({ retries: 0 });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { router, client, user };
}
