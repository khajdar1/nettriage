import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import type { Org } from "../../orgs/org";
import { assignableRoles } from "../../orgs/permissions";
import { ROLE_LABELS, type Role, roleLabel } from "../../orgs/roles";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatDate } from "../../ui/format";
import { Loading } from "../../ui/Loading";

function invitationsKey(orgId: string) {
  return ["invitations", orgId] as const;
}

/** The link to send, shown once: the API keeps only its hash (spec §6.3). */
function InviteLink({ url, email }: { url: string; email: string }) {
  const [copied, setCopied] = useState<boolean | null>(null);
  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div className="invite-link">
      <p>
        Send this link to <strong>{email}</strong>. It works once, for 7 days, and only for that
        email address. It won't be shown again.
      </p>
      <div className="inline-form">
        <input
          className="mono"
          readOnly
          aria-label="Invitation link"
          value={url}
          onFocus={(event) => event.target.select()}
        />
        <button type="button" className="button" onClick={() => void copy()}>
          Copy link
        </button>
      </div>
      <p role="status" className="muted">
        {copied === true && "Copied."}
        {copied === false && "Couldn't copy: select the link and copy it yourself."}
      </p>
    </div>
  );
}

function InviteForm({ org }: { org: Org }) {
  const roles = assignableRoles(org.role);
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const invite = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/orgs/{org_id}/invitations", {
          params: { path: { org_id: org.id } },
          body: { email: email.trim(), role },
        }),
      ),
    onSuccess: async () => {
      setEmail("");
      await queryClient.invalidateQueries({ queryKey: invitationsKey(org.id) });
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    invite.mutate();
  }
  return (
    <>
      <form className="inline-form" onSubmit={submit}>
        <div className="field">
          <label htmlFor="invite-email">Email address</label>
          <input
            id="invite-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
            maxLength={320}
          />
        </div>
        <div className="field">
          <label htmlFor="invite-role">Role</label>
          <select
            id="invite-role"
            value={role}
            onChange={(event) => setRole(event.target.value as Role)}
          >
            {roles.map((option) => (
              <option key={option} value={option}>
                {ROLE_LABELS[option]}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" className="button button-primary" disabled={invite.isPending}>
          Invite
        </button>
      </form>
      {invite.isError && <ErrorNotice error={invite.error} />}
      {invite.data !== undefined && (
        <InviteLink url={invite.data.invite_url} email={invite.data.invitation.email} />
      )}
    </>
  );
}

/** Pending invitations, inviting someone, and revoking an invitation (spec §6.3, §7). */
export function Invitations({ org }: { org: Org }) {
  const queryClient = useQueryClient();
  const invitations = useQuery({
    queryKey: invitationsKey(org.id),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/invitations", {
          params: { path: { org_id: org.id } },
        }),
      ),
  });
  const revoke = useMutation({
    mutationFn: async (invitationId: string) =>
      unwrap(
        await api.DELETE("/api/v1/orgs/{org_id}/invitations/{invitation_id}", {
          params: { path: { org_id: org.id, invitation_id: invitationId } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: invitationsKey(org.id) }),
  });
  return (
    <section className="card" aria-labelledby="invitations">
      <h2 id="invitations">Invitations</h2>
      <InviteForm org={org} />
      {revoke.isError && <ErrorNotice error={revoke.error} />}
      {invitations.isPending && <Loading />}
      {invitations.isError && <ErrorNotice error={invitations.error} />}
      {invitations.data !== undefined &&
        (invitations.data.invitations.length === 0 ? (
          <p className="muted">No pending invitations.</p>
        ) : (
          <table className="table">
            <caption className="visually-hidden">Pending invitations</caption>
            <thead>
              <tr>
                <th scope="col">Email</th>
                <th scope="col">Role</th>
                <th scope="col">Expires</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {invitations.data.invitations.map((invitation) => (
                <tr key={invitation.id}>
                  <td>{invitation.email}</td>
                  <td>{roleLabel(invitation.role)}</td>
                  <td>{formatDate(invitation.expires_at)}</td>
                  <td>
                    <button
                      type="button"
                      className="button"
                      aria-label={`Revoke the invitation for ${invitation.email}`}
                      disabled={revoke.isPending}
                      onClick={() => revoke.mutate(invitation.id)}
                    >
                      Revoke
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
    </section>
  );
}
