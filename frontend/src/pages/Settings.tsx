import { useMutation } from "@tanstack/react-query";
import { signOut, useMe } from "../auth/session";
import { ErrorNotice } from "../ui/ErrorNotice";
import { usePageTitle } from "../ui/usePageTitle";

/** `/app/settings`: the account, and "sign out everywhere" (spec §2.2, §10). */
export function Settings() {
  usePageTitle("Settings");
  const me = useMe();
  const everywhere = useMutation({ mutationFn: () => signOut({ everywhere: true }) });
  return (
    <main id="main" className="page narrow">
      <h1>Settings</h1>
      <section className="card" aria-labelledby="account">
        <h2 id="account">Your account</h2>
        <p>
          Signed in as <strong>{me.data?.user.email}</strong>.
        </p>
      </section>
      <section className="card" aria-labelledby="everywhere">
        <h2 id="everywhere">Sign out everywhere</h2>
        <p>Ends your sessions in every browser and on every device, this one included.</p>
        <button
          type="button"
          className="button button-danger"
          disabled={everywhere.isPending}
          onClick={() => everywhere.mutate()}
        >
          Sign out everywhere
        </button>
        {everywhere.isError && <ErrorNotice error={everywhere.error} />}
      </section>
    </main>
  );
}
