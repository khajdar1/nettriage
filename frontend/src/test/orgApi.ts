/** The fake API for a person signed in to Acme Security as one of its members. */
import type { Me } from "../auth/session";
import type { Member } from "../pages/org/MemberRow";
import { type FakeApi, type Handler, fakeApi } from "./fakeApi";
import { ACME, ADMIN, ME, ORG_ID, OWNER, VIEWER, memberOf, org } from "./fixtures";

export const ORG = `/api/v1/orgs/${ORG_ID}`;

export function signedInAs(self: Member, extra: Record<string, Handler> = {}): FakeApi {
  const me: Me = {
    ...memberOf({ ...ACME, role: self.role }),
    user: { ...ME.user, id: self.user_id, email: self.email },
  };
  return fakeApi({
    "GET /api/v1/me": { body: me },
    [`GET ${ORG}`]: { body: org(self.role) },
    [`GET ${ORG}/members`]: { body: { members: [OWNER, ADMIN, VIEWER] } },
    [`GET ${ORG}/invitations`]: { body: { invitations: [] } },
    ...extra,
  });
}
