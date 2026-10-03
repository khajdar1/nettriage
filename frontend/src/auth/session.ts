/** The signed-in person (spec §4.1, §6.2): who they are, and signing in and out. */
import { useQuery } from "@tanstack/react-query";
import { api, setCsrfToken } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Me = components["schemas"]["MeOut"];
export type Membership = components["schemas"]["MembershipOut"];

export const ME_KEY = ["me"] as const;

/** The signed-in person, or `null` when signed out. Keeps their CSRF token for the client. */
export async function fetchMe(): Promise<Me | null> {
  const result = await api.GET("/api/v1/me");
  if (result.response.status === 401) {
    setCsrfToken(null);
    return null;
  }
  const me = unwrap(result);
  setCsrfToken(me.csrf_token);
  return me;
}

export function useMe() {
  return useQuery({ queryKey: ME_KEY, queryFn: fetchMe, staleTime: 5 * 60_000 });
}

/** Signing in is a page load: the API sends the browser to Cognito's managed login. */
export function signInUrl(returnTo: string): string {
  return `/api/auth/login?return_to=${encodeURIComponent(returnTo)}`;
}

/** Where the browser goes next; tests replace `go`. */
export const browser = {
  go(url: string): void {
    window.location.assign(url);
  },
};

/** Ends this session, or every session the person has, then signs out of Cognito as well. */
export async function signOut({ everywhere }: { everywhere: boolean }): Promise<void> {
  const result = everywhere
    ? await api.POST("/api/auth/logout-all")
    : await api.POST("/api/auth/logout");
  const { logout_url } = unwrap(result);
  setCsrfToken(null);
  browser.go(logout_url);
}
