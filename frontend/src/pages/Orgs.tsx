import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router";
import { api, idempotencyKey } from "../api/client";
import { unwrap } from "../api/problem";
import { ME_KEY, useMe } from "../auth/session";
import { roleLabel } from "../orgs/roles";
import { ErrorNotice } from "../ui/ErrorNotice";
import { usePageTitle } from "../ui/usePageTitle";

/** A person belongs to at most three organizations (spec §2.1, §7). */
export const MAX_ORGS = 3;

function CreateOrg() {
  const [name, setName] = useState("");
  // One key per form: a resent request returns the org already created (spec §7).
  const [key] = useState(idempotencyKey);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: async (orgName: string) =>
      unwrap(
        await api.POST("/api/v1/orgs", {
          body: { name: orgName },
          headers: { "Idempotency-Key": key },
        }),
      ),
    onSuccess: async (org) => {
      await queryClient.invalidateQueries({ queryKey: ME_KEY });
      await navigate(`/app/orgs/${org.id}`);
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate(name.trim());
  }
  return (
    <section className="card" aria-labelledby="create-org">
      <h2 id="create-org">Create an organization</h2>
      <form className="inline-form" onSubmit={submit}>
        <div className="field">
          <label htmlFor="org-name">Organization name</label>
          <input
            id="org-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            maxLength={100}
          />
        </div>
        <button type="submit" className="button button-primary" disabled={create.isPending}>
          Create organization
        </button>
      </form>
      {create.isError && <ErrorNotice error={create.error} />}
    </section>
  );
}

/** `/app`: the person's organizations, and creating one (spec §10). */
export function Orgs() {
  usePageTitle("Your organizations");
  const me = useMe();
  const memberships = me.data?.memberships ?? [];
  return (
    <main id="main" className="page">
      <h1>Your organizations</h1>
      {memberships.length === 0 ? (
        <p>Create your first organization, or open an invitation link someone sent you.</p>
      ) : (
        <ul className="org-list">
          {memberships.map((membership) => (
            <li key={membership.org_id}>
              <Link to={`/app/orgs/${membership.org_id}`}>{membership.name}</Link>
              <span className="badge">{roleLabel(membership.role)}</span>
            </li>
          ))}
        </ul>
      )}
      {memberships.length < MAX_ORGS ? (
        <CreateOrg />
      ) : (
        <p className="muted">You belong to {MAX_ORGS} organizations, the most there can be.</p>
      )}
    </main>
  );
}
