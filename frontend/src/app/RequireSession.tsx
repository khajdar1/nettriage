import type { ReactNode } from "react";
import { useLocation } from "react-router";
import { signInUrl, useMe } from "../auth/session";
import { ErrorNotice } from "../ui/ErrorNotice";
import { Loading } from "../ui/Loading";
import { StandaloneFrame, StandalonePage } from "../ui/StandalonePage";

/** The signed-in part of the app: anyone else is asked to sign in, then brought back here. */
export function RequireSession({ children }: { children: ReactNode }) {
  const me = useMe();
  const location = useLocation();
  if (me.data === undefined) {
    // A check that fails later keeps the page and what's typed in it: only a first load fails.
    return (
      <StandaloneFrame>
        {me.isError ? <ErrorNotice error={me.error} /> : <Loading />}
      </StandaloneFrame>
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
