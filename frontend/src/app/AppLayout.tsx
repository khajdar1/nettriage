import { useMutation } from "@tanstack/react-query";
import { Link, Outlet } from "react-router";
import { signOut, useMe } from "../auth/session";
import { ErrorNotice } from "../ui/ErrorNotice";

/** The frame of every signed-in page: the product, the person, and signing out. */
export function AppLayout() {
  const me = useMe();
  const leaving = useMutation({ mutationFn: () => signOut({ everywhere: false }) });
  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <Link to="/app" className="brand">
          NetTriage
        </Link>
        <nav className="account" aria-label="Account">
          <span className="who">{me.data?.user.email}</span>
          <Link to="/app/settings">Account settings</Link>
          <button
            type="button"
            className="button button-quiet"
            disabled={leaving.isPending}
            onClick={() => leaving.mutate()}
          >
            Sign out
          </button>
        </nav>
      </header>
      {leaving.isError && (
        <div className="page">
          <ErrorNotice error={leaving.error} />
        </div>
      )}
      <Outlet />
    </div>
  );
}
