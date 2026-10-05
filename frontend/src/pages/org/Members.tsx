import { useMe } from "../../auth/session";
import { useMembers } from "../../orgs/members";
import { useOrg } from "../../orgs/org";
import { canInvite } from "../../orgs/permissions";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Loading } from "../../ui/Loading";
import { usePageTitle } from "../../ui/usePageTitle";
import { Invitations } from "./Invitations";
import { MemberRow } from "./MemberRow";

/** `/app/orgs/:org/members`: members, their roles, and invitations (spec §10). */
export function Members() {
  const org = useOrg();
  usePageTitle(`Members · ${org.name}`);
  const me = useMe();
  const members = useMembers(org.id);
  return (
    <main id="main" className="page">
      <div className="page-head">
        <h1>Members</h1>
        {members.data !== undefined && (
          <span className="muted">
            {members.data.length === 1 ? "1 member" : `${members.data.length} members`}
          </span>
        )}
      </div>
      {members.isPending && <Loading />}
      {members.isError && <ErrorNotice error={members.error} />}
      {members.data !== undefined && (
        <table className="table">
          <caption className="visually-hidden">Members of {org.name}</caption>
          <thead>
            <tr>
              <th scope="col">Member</th>
              <th scope="col">Role</th>
              <th scope="col">Joined</th>
              <th scope="col">
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {members.data.map((member) => (
              <MemberRow
                key={member.user_id}
                org={org}
                member={member}
                isSelf={member.user_id === me.data?.user.id}
              />
            ))}
          </tbody>
        </table>
      )}
      {canInvite(org.role) && <Invitations org={org} />}
    </main>
  );
}
