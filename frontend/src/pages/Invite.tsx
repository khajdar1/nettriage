import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import { ME_KEY, signInUrl, useMe } from "../auth/session";
import { ErrorNotice } from "../ui/ErrorNotice";
import { Loading } from "../ui/Loading";
import { usePageTitle } from "../ui/usePageTitle";

// The token travels in the link's fragment, which browsers never send to a server (spec §6.3).
// Signing in leaves the app, so it waits in this tab's sessionStorage until it's accepted.
const STORED = "nettriage.invitation";

function keepToken(hash: string): string | null {
  const fromLink = hash.startsWith("#") ? hash.slice(1) : "";
  try {
    if (fromLink !== "") {
      sessionStorage.setItem(STORED, fromLink);
      return fromLink;
    }
    return sessionStorage.getItem(STORED);
  } catch {
    return fromLink === "" ? null : fromLink;
  }
}

function forgetToken(): void {
  try {
    sessionStorage.removeItem(STORED);
  } catch {
    // Storage is blocked, so nothing was kept.
  }
}

function Page({ title, children }: { title: string; children: ReactNode }) {
  usePageTitle(title);
  return (
    <main id="main" className="page narrow">
      <h1>{title}</h1>
      {children}
    </main>
  );
}

/** `/invite#<token>`: signs the person in if needed, then accepts the invitation (spec §10). */
export function Invite() {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [token] = useState(() => keepToken(location.hash));
  const me = useMe();
  const accept = useMutation({
    mutationFn: async (value: string) =>
      unwrap(await api.POST("/api/v1/invitations/accept", { body: { token: value } })),
    onSuccess: async (org) => {
      forgetToken();
      await queryClient.invalidateQueries({ queryKey: ME_KEY });
      await navigate(`/app/orgs/${org.id}`, { replace: true });
    },
  });
  const started = useRef(false);

  useEffect(() => {
    // Take the token out of the address bar and the history.
    if (location.hash !== "") {
      void navigate({ pathname: "/invite" }, { replace: true });
    }
  }, [location.hash, navigate]);

  useEffect(() => {
    if (token !== null && me.data && !started.current) {
      started.current = true;
      accept.mutate(token);
    }
  }, [token, me.data, accept]);

  if (token === null) {
    return (
      <Page title="Invitation link incomplete">
        <p>Open the link from your invitation again: it ends with a long code after a #.</p>
      </Page>
    );
  }
  if (me.isPending) {
    return (
      <Page title="You're invited">
        <Loading />
      </Page>
    );
  }
  if (me.isError) {
    return (
      <Page title="You're invited">
        <ErrorNotice error={me.error} />
      </Page>
    );
  }
  if (me.data === null) {
    return (
      <Page title="You're invited">
        <p>
          Sign in, or sign up with the email address the invitation was sent to, and you'll join the
          organization.
        </p>
        <a className="button button-primary" href={signInUrl("/invite")}>
          Sign in to accept
        </a>
      </Page>
    );
  }
  if (accept.isError) {
    return (
      <Page title="This invitation can't be used">
        <ErrorNotice error={accept.error} />
        <p>
          An invitation works once, for 7 days, and only for the email address it was sent to. Ask
          for a new one if you need it.
        </p>
        <Link to="/app">Your organizations</Link>
      </Page>
    );
  }
  return (
    <Page title="You're invited">
      <p role="status">Accepting your invitation…</p>
    </Page>
  );
}
