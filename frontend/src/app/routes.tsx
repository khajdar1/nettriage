import { Navigate, type RouteObject } from "react-router";
import { Invite } from "../pages/Invite";
import { Landing } from "../pages/Landing";
import { NotFound } from "../pages/NotFound";
import { AuditLog } from "../pages/org/AuditLog";
import { FindingPage } from "../pages/org/finding/FindingPage";
import { Findings } from "../pages/org/Findings";
import { Members } from "../pages/org/Members";
import { OrgLayout } from "../pages/org/OrgLayout";
import { OrgSettings } from "../pages/org/OrgSettings";
import { Uploads } from "../pages/org/Uploads";
import { Orgs } from "../pages/Orgs";
import { Settings } from "../pages/Settings";
import { AppLayout } from "./AppLayout";
import { RequireSession } from "./RequireSession";

/** Every page of the app (spec §10). CloudFront serves index.html for each of these paths. */
export const routes: RouteObject[] = [
  { path: "/", element: <Landing /> },
  { path: "/invite", element: <Invite /> },
  {
    path: "/app",
    element: (
      <RequireSession>
        <AppLayout />
      </RequireSession>
    ),
    children: [
      { index: true, element: <Orgs /> },
      { path: "settings", element: <Settings /> },
      {
        path: "orgs/:orgId",
        element: <OrgLayout />,
        children: [
          { index: true, element: <Navigate to="findings" replace /> },
          { path: "findings", element: <Findings /> },
          { path: "findings/:findingId", element: <FindingPage /> },
          { path: "uploads", element: <Uploads /> },
          { path: "members", element: <Members /> },
          { path: "audit", element: <AuditLog /> },
          { path: "settings", element: <OrgSettings /> },
        ],
      },
    ],
  },
  { path: "*", element: <NotFound /> },
];
