import { useMutation } from "@tanstack/react-query";
import { signOut, useMe } from "../auth/session";
import { ErrorNotice } from "../ui/ErrorNotice";
import { usePageTitle } from "../ui/usePageTitle";

/** `/app/settings`: the account, and "sign out everywhere" (spec §2.2, §10). */
export function Settings() {
  usePageTitle("Account settings");
  const me = useMe();
  const everywhere = useMutation({ mutationFn: () => signOut({ everywhere: true }) });
  return (
    <main id="main" className="page">
      <h1>Account settings</h1>
      <section className="setting" aria-labelledby="account">
        <div className="setting-what">
          <h2 id="account">Your account</h2>
          <p>The address you sign in with, verified by Cognito.</p>
        </div>
        <div className="setting-how">
          <p>
            Signed in as <strong>{me.data?.user.email}</strong>
          </p>
        </div>
      </section>
      <section className="setting" aria-labelledby="everywhere">
        <div className="setting-what">
          <h2 id="everywhere">Sign out everywhere</h2>
          <p>Ends your sessions in every browser and on every device, this one included.</p>
        </div>
        <div className="setting-how">
          <button
            type="button"
            className="button button-danger"
            disabled={everywhere.isPending}
            onClick={() => everywhere.mutate()}
          >
            Sign out everywhere
          </button>
          {everywhere.isError && <ErrorNotice error={everywhere.error} />}
        </div>
      </section>
    </main>
  );
}
