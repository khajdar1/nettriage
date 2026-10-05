import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router";
import { api, idempotencyKey } from "../api/client";
import { unwrap } from "../api/problem";
import { ME_KEY, useMe } from "../auth/session";
import { ErrorNotice } from "../ui/ErrorNotice";
import { usePageTitle } from "../ui/usePageTitle";
import { AssignedToYou } from "./home/AssignedToYou";
import { OrgCard } from "./home/OrgCard";

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
    <section className="setting" aria-labelledby="create-org">
      <div className="setting-what">
        <h2 id="create-org">Create an organization</h2>
        <p>You'll be its owner, and can invite your team.</p>
      </div>
      <div className="setting-how">
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
      </div>
    </section>
  );
}

/**
 * `/app`, the home: the findings assigned to the person across their organizations, then each
 * organization at a glance, and creating one (spec §10, Plan 6d).
 */
export function Orgs() {
  usePageTitle("Home");
  const me = useMe();
  const memberships = me.data?.memberships ?? [];
  return (
    <main id="main" className="page home">
      <h1 className="visually-hidden">Home</h1>
      {memberships.length > 0 && <AssignedToYou memberships={memberships} />}
      <section className="home-section" aria-labelledby="orgs-title">
        <div className="page-head">
          <h2 id="orgs-title" className="page-title">
            Organizations
          </h2>
        </div>
        {memberships.length === 0 ? (
          <p>Create your first organization, or open an invitation link someone sent you.</p>
        ) : (
          <div className="org-cards">
            {memberships.map((membership) => (
              <OrgCard key={membership.org_id} membership={membership} />
            ))}
          </div>
        )}
      </section>
      {memberships.length < MAX_ORGS ? (
        <CreateOrg />
      ) : (
        <p className="muted">You belong to {MAX_ORGS} organizations, the most there can be.</p>
      )}
    </main>
  );
}
