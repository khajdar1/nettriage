import { Link, NavLink, Outlet, useParams } from "react-router";
import { ApiError } from "../../api/problem";
import { useOrgQuery } from "../../orgs/org";
import { roleLabel } from "../../orgs/roles";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Loading } from "../../ui/Loading";
import { usePageTitle } from "../../ui/usePageTitle";

function OrgNotFound() {
  usePageTitle("Organization not found");
  return (
    <main id="main" className="page narrow">
      <h1>Organization not found</h1>
      <p>It may have been deleted, or you're no longer a member.</p>
      <Link to="/app">Your organizations</Link>
    </main>
  );
}

/** The frame of an organization's pages: its name, the person's role in it, and its tabs. */
export function OrgLayout() {
  const { orgId = "" } = useParams();
  const org = useOrgQuery(orgId);
  if (org.isPending) {
    return (
      <main id="main" className="page">
        <Loading />
      </main>
    );
  }
  if (org.isError) {
    if (org.error instanceof ApiError && org.error.status === 404) {
      return <OrgNotFound />;
    }
    return (
      <main id="main" className="page">
        <ErrorNotice error={org.error} />
      </main>
    );
  }
  return (
    <>
      <div className="org-bar">
        <div className="org-bar-inner">
          <p className="org-name">
            {org.data.name}
            <span className="badge">{roleLabel(org.data.role)}</span>
          </p>
          <nav className="tabs" aria-label="Organization">
            <NavLink to="findings">Findings</NavLink>
            <NavLink to="uploads">Uploads</NavLink>
            <NavLink to="members">Members</NavLink>
            <NavLink to="settings">Settings</NavLink>
          </nav>
        </div>
      </div>
      <Outlet context={org.data} />
    </>
  );
}
