import type { ReactNode } from "react";
import { useLocation } from "react-router";
import { signInUrl, useMe } from "../auth/session";
import { ErrorNotice } from "../ui/ErrorNotice";
import { Loading } from "../ui/Loading";
import { usePageTitle } from "../ui/usePageTitle";

function SignInPrompt({ returnTo }: { returnTo: string }) {
  usePageTitle("Sign in");
  return (
    <main id="main" className="page narrow">
      <h1>Sign in to continue</h1>
      <p>You're signed out, or your session ended.</p>
      <a className="button button-primary" href={signInUrl(returnTo)}>
        Sign in
      </a>
    </main>
  );
}

/** The signed-in part of the app: anyone else is asked to sign in, then brought back here. */
export function RequireSession({ children }: { children: ReactNode }) {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) {
    return (
      <main id="main" className="page">
        <Loading />
      </main>
    );
  }
  if (me.isError) {
    return (
      <main id="main" className="page narrow">
        <ErrorNotice error={me.error} />
      </main>
    );
  }
  if (me.data === null) {
    return <SignInPrompt returnTo={location.pathname + location.search} />;
  }
  return children;
}
