import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import { useMe } from "../../auth/session";
import { useOrg } from "../../orgs/org";
import { canInvite } from "../../orgs/permissions";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Loading } from "../../ui/Loading";
import { usePageTitle } from "../../ui/usePageTitle";
import { Invitations } from "./Invitations";
import { MemberRow, membersKey } from "./MemberRow";

/** `/app/orgs/:org/members`: members, their roles, and invitations (spec §10). */
export function Members() {
  const org = useOrg();
  usePageTitle(`Members · ${org.name}`);
  const me = useMe();
  const members = useQuery({
    queryKey: membersKey(org.id),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/members", { params: { path: { org_id: org.id } } }),
      ),
  });
  return (
    <main id="main" className="page">
      <h1>Members</h1>
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
            {members.data.members.map((member) => (
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
