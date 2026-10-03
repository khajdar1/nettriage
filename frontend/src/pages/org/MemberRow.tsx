import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { flushSync } from "react-dom";
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import type { components } from "../../api/schema";
import type { Org } from "../../orgs/org";
import { assignableRoles, canManage } from "../../orgs/permissions";
import { ROLE_LABELS, type Role, roleLabel } from "../../orgs/roles";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatDate } from "../../ui/format";

export type Member = components["schemas"]["MemberOut"];

export function membersKey(orgId: string) {
  return ["members", orgId] as const;
}

/**
 * One member: their role, which an Owner or Admin may choose and then save, and removing them.
 * A role is saved only with its button, because keyboards change a closed select on every arrow
 * key, and each change would otherwise be saved and audited.
 */
export function MemberRow({ org, member, isSelf }: { org: Org; member: Member; isSelf: boolean }) {
  const queryClient = useQueryClient();
  const [chosen, setChosen] = useState<Role>(member.role);
  const [confirming, setConfirming] = useState(false);
  const roleSelect = useRef<HTMLSelectElement>(null);
  const removeButton = useRef<HTMLButtonElement>(null);
  const path = { org_id: org.id, user_id: member.user_id };
  const refresh = () => queryClient.invalidateQueries({ queryKey: membersKey(org.id) });
  const changeRole = useMutation({
    mutationFn: async (role: Role) =>
      unwrap(
        await api.PATCH("/api/v1/orgs/{org_id}/members/{user_id}", {
          params: { path },
          body: { role },
        }),
      ),
    onSuccess: async () => {
      await refresh();
      roleSelect.current?.focus();
    },
  });
  const remove = useMutation({
    mutationFn: async () =>
      unwrap(await api.DELETE("/api/v1/orgs/{org_id}/members/{user_id}", { params: { path } })),
    onSuccess: refresh,
  });
  function cancel() {
    flushSync(() => setConfirming(false));
    removeButton.current?.focus();
  }
  const who = member.display_name ?? member.email;
  const manageable = canManage(org.role, member.role, isSelf);
  const error = changeRole.error ?? remove.error;
  return (
    <>
      <tr>
        <td>
          {who}
          {isSelf && <span className="badge">You</span>}
          {member.display_name !== null && <div className="muted">{member.email}</div>}
        </td>
        <td>
          {manageable ? (
            <span className="actions">
              <select
                ref={roleSelect}
                aria-label={`Role of ${who}`}
                value={chosen}
                onChange={(event) => setChosen(event.target.value as Role)}
              >
                {assignableRoles(org.role).map((role) => (
                  <option key={role} value={role}>
                    {ROLE_LABELS[role]}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="button"
                aria-label={`Save the role of ${who}`}
                disabled={chosen === member.role || changeRole.isPending}
                onClick={() => changeRole.mutate(chosen)}
              >
                Save role
              </button>
            </span>
          ) : (
            roleLabel(member.role)
          )}
        </td>
        <td>{formatDate(member.joined_at)}</td>
        <td>
          {manageable &&
            (confirming ? (
              <span className="actions">
                <button
                  type="button"
                  className="button button-danger"
                  disabled={remove.isPending}
                  onClick={() => remove.mutate()}
                  autoFocus
                >
                  Yes, remove {who}
                </button>
                <button type="button" className="button" onClick={cancel}>
                  Cancel
                </button>
              </span>
            ) : (
              <button
                ref={removeButton}
                type="button"
                className="button"
                aria-label={`Remove ${who}`}
                onClick={() => setConfirming(true)}
              >
                Remove
              </button>
            ))}
        </td>
      </tr>
      {error !== null && (
        <tr>
          <td colSpan={4}>
            <ErrorNotice error={error} />
          </td>
        </tr>
      )}
    </>
  );
}
