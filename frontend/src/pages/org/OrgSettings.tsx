import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { useNavigate } from "react-router";
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import { ME_KEY, useMe } from "../../auth/session";
import { type Org, orgKey, useOrg } from "../../orgs/org";
import { canDelete, canRename } from "../../orgs/permissions";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { usePageTitle } from "../../ui/usePageTitle";

function Rename({ org }: { org: Org }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(org.name);
  const rename = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.PATCH("/api/v1/orgs/{org_id}", {
          params: { path: { org_id: org.id } },
          body: { name: name.trim() },
        }),
      ),
    onSuccess: async (renamed) => {
      queryClient.setQueryData(orgKey(org.id), renamed);
      await queryClient.invalidateQueries({ queryKey: ME_KEY });
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    rename.mutate();
  }
  return (
    <section className="setting" aria-labelledby="rename">
      <div className="setting-what">
        <h2 id="rename">Name</h2>
        <p>What everyone in it sees.</p>
      </div>
      <div className="setting-how">
        <form className="inline-form" onSubmit={submit}>
          <div className="field">
            <label htmlFor="org-rename">Organization name</label>
            <input
              id="org-rename"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              maxLength={100}
            />
          </div>
          <button type="submit" className="button button-primary" disabled={rename.isPending}>
            Save
          </button>
        </form>
        <p role="status" className="muted">
          {rename.isSuccess && "Saved."}
        </p>
        {rename.isError && <ErrorNotice error={rename.error} />}
      </div>
    </section>
  );
}

/** Leaving or deleting ends the person's access: back to their organizations. */
function useGone() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return async (orgId: string) => {
    queryClient.removeQueries({ queryKey: orgKey(orgId) });
    await queryClient.invalidateQueries({ queryKey: ME_KEY });
    await navigate("/app");
  };
}

function Leave({ org, userId }: { org: Org; userId: string }) {
  const gone = useGone();
  const [confirming, setConfirming] = useState(false);
  const leaveButton = useRef<HTMLButtonElement>(null);
  function cancel() {
    flushSync(() => setConfirming(false));
    leaveButton.current?.focus();
  }
  const leave = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.DELETE("/api/v1/orgs/{org_id}/members/{user_id}", {
          params: { path: { org_id: org.id, user_id: userId } },
        }),
      ),
    onSuccess: () => gone(org.id),
  });
  return (
    <section className="setting" aria-labelledby="leave">
      <div className="setting-what">
        <h2 id="leave">Leave this organization</h2>
        <p>You lose access to its uploads and findings until someone invites you again.</p>
      </div>
      <div className="setting-how">
        {confirming ? (
          <div className="actions">
            <button
              type="button"
              className="button button-danger"
              disabled={leave.isPending}
              onClick={() => leave.mutate()}
              autoFocus
            >
              Yes, leave {org.name}
            </button>
            <button type="button" className="button" onClick={cancel}>
              Cancel
            </button>
          </div>
        ) : (
          <button
            ref={leaveButton}
            type="button"
            className="button"
            onClick={() => setConfirming(true)}
          >
            Leave
          </button>
        )}
        {leave.isError && <ErrorNotice error={leave.error} />}
      </div>
    </section>
  );
}

function Delete({ org }: { org: Org }) {
  const gone = useGone();
  const [typed, setTyped] = useState("");
  const remove = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.DELETE("/api/v1/orgs/{org_id}", {
          params: { path: { org_id: org.id }, query: { confirm_name: typed } },
        }),
      ),
    onSuccess: () => gone(org.id),
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    remove.mutate();
  }
  return (
    <section className="setting setting-danger" aria-labelledby="delete">
      <div className="setting-what">
        <h2 id="delete">Delete this organization</h2>
        <p>
          This deletes its members, invitations, uploads, findings and AI explanations. It can't be
          undone.
        </p>
      </div>
      <div className="setting-how">
        <form className="inline-form" onSubmit={submit}>
          <div className="field">
            <label htmlFor="org-confirm">Type {org.name} to confirm</label>
            <input
              id="org-confirm"
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              autoComplete="off"
            />
          </div>
          <button
            type="submit"
            className="button button-danger"
            disabled={typed !== org.name || remove.isPending}
          >
            Delete organization
          </button>
        </form>
        {remove.isError && <ErrorNotice error={remove.error} />}
      </div>
    </section>
  );
}

/** `/app/orgs/:org/settings`: rename, leave or delete the organization (spec §2.2). */
export function OrgSettings() {
  const org = useOrg();
  usePageTitle(`Settings · ${org.name}`);
  const me = useMe();
  return (
    <main id="main" className="page">
      <h1>Settings</h1>
      {canRename(org.role) && <Rename org={org} />}
      {me.data && <Leave org={org} userId={me.data.user.id} />}
      {canDelete(org.role) && <Delete org={org} />}
    </main>
  );
}
