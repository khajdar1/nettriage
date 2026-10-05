import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { api } from "../../../api/client";
import { ApiError, unwrap } from "../../../api/problem";
import { type Finding, findingKey } from "../../../findings/finding";
import { findingsKey } from "../../../findings/findings";
import { STATUSES, type Status, statusLabel } from "../../../findings/vocabulary";
import { type Member, memberName, useMembers } from "../../../orgs/members";
import type { Org } from "../../../orgs/org";
import { canContribute } from "../../../orgs/permissions";
import { ErrorNotice } from "../../../ui/ErrorNotice";

const STALE =
  "Someone changed this finding while you had it open. It now shows their change; make yours again if it still applies.";

type Change = { status?: Status; assignee_id?: string | null };

/**
 * Status and assignee, chosen and then saved together (spec §7): the change names the version it
 * was made on, so it never overwrites someone else's newer one. Only owners, admins and analysts
 * can be assigned (the owner's decision, Plan 4c).
 */
function TriageForm({ org, finding, members }: { org: Org; finding: Finding; members?: Member[] }) {
  const queryClient = useQueryClient();
  const saved = finding.assignee_id ?? "";
  const [status, setStatus] = useState<Status>(finding.status);
  const [assignee, setAssignee] = useState(saved);
  const [version, setVersion] = useState(finding.version);
  // A newer version, saved here or by someone else, replaces what was chosen.
  if (finding.version !== version) {
    setVersion(finding.version);
    setStatus(finding.status);
    setAssignee(saved);
  }
  const save = useMutation({
    mutationFn: async (change: Change) =>
      unwrap(
        await api.PATCH("/api/v1/orgs/{org_id}/findings/{finding_id}", {
          params: {
            path: { org_id: org.id, finding_id: finding.id },
            header: { "if-match": `"${finding.version}"` },
          },
          body: change,
        }),
      ),
    onSuccess: (updated) => {
      queryClient.setQueryData(findingKey(org.id, finding.id), updated);
      void queryClient.invalidateQueries({ queryKey: findingsKey(org.id) });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 412) {
        void queryClient.invalidateQueries({ queryKey: findingKey(org.id, finding.id) });
      }
    },
  });
  const choices = (members ?? []).filter((member) => member.role !== "viewer");
  const change: Change = {};
  if (status !== finding.status) {
    change.status = status;
  }
  if (assignee !== saved) {
    change.assignee_id = assignee === "" ? null : assignee;
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate(change);
  }
  return (
    <form onSubmit={submit}>
      <div className="field">
        <label htmlFor="triage-status">Status</label>
        <select
          id="triage-status"
          value={status}
          onChange={(event) => setStatus(event.target.value as Status)}
        >
          {STATUSES.map((choice) => (
            <option key={choice} value={choice}>
              {statusLabel(choice)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="triage-assignee">Assignee</label>
        <select
          id="triage-assignee"
          value={assignee}
          onChange={(event) => setAssignee(event.target.value)}
        >
          <option value="">Unassigned</option>
          {choices.map((member) => (
            <option key={member.user_id} value={member.user_id}>
              {member.display_name ?? member.email}
            </option>
          ))}
          {saved !== "" && !choices.some((member) => member.user_id === saved) && (
            <option value={saved}>{memberName(members, saved)}</option>
          )}
        </select>
      </div>
      <button
        type="submit"
        className="button button-primary"
        disabled={Object.keys(change).length === 0 || save.isPending}
      >
        Save changes
      </button>
      {save.isError &&
        (save.error instanceof ApiError && save.error.status === 412 ? (
          <p role="alert" className="notice notice-error">
            {STALE}
          </p>
        ) : (
          <ErrorNotice error={save.error} />
        ))}
    </form>
  );
}

/** Where the finding stands: changed by contributors, read by everyone else (spec §10). */
export function Triage({ org, finding }: { org: Org; finding: Finding }) {
  const members = useMembers(org.id);
  return (
    <section className="triage" aria-labelledby="triage-title">
      <h2 id="triage-title">Triage</h2>
      {canContribute(org.role) ? (
        <TriageForm org={org} finding={finding} members={members.data} />
      ) : (
        <dl className="facts-list">
          <dt>Status</dt>
          <dd>{statusLabel(finding.status)}</dd>
          <dt>Assignee</dt>
          <dd>
            {finding.assignee_id === null
              ? "Unassigned"
              : memberName(members.data, finding.assignee_id)}
          </dd>
        </dl>
      )}
    </section>
  );
}
