import type { ReactNode } from "react";
import { useLocation } from "react-router";
import { signInUrl, useMe } from "../auth/session";
import { ErrorNotice } from "../ui/ErrorNotice";
import { Loading } from "../ui/Loading";
import { StandalonePage } from "../ui/StandalonePage";
import { AppTop } from "./AppTop";

/** The signed-in part of the app: anyone else is asked to sign in, then brought back here. */
export function RequireSession({ children }: { children: ReactNode }) {
  const me = useMe();
  const location = useLocation();
  if (me.data === undefined) {
    // A check that fails later keeps the page and what's typed in it: only a first load fails.
    // It is framed like the signed-in pages, so nothing moves when the check ends.
    return (
      <div className="shell">
        <AppTop />
        <main id="main" className="page">
          {me.isError ? <ErrorNotice error={me.error} /> : <Loading />}
        </main>
      </div>
    );
  }
  if (me.data === null) {
    return (
      <StandalonePage title="Sign in to continue">
        <p>You're signed out, or your session ended.</p>
        <a className="button button-primary" href={signInUrl(location.pathname + location.search)}>
          Sign in
        </a>
      </StandalonePage>
    );
  }
  return children;
}
