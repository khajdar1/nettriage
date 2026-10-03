import { Navigate, type RouteObject } from "react-router";
import { Landing } from "../pages/Landing";
import { NotFound } from "../pages/NotFound";
import { Members } from "../pages/org/Members";
import { OrgLayout } from "../pages/org/OrgLayout";
import { OrgSettings } from "../pages/org/OrgSettings";
import { Orgs } from "../pages/Orgs";
import { Settings } from "../pages/Settings";
import { AppLayout } from "./AppLayout";
import { RequireSession } from "./RequireSession";

/** Every page of the app (spec §10). CloudFront serves index.html for each of these paths. */
export const routes: RouteObject[] = [
  { path: "/", element: <Landing /> },
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
          { index: true, element: <Navigate to="members" replace /> },
          { path: "members", element: <Members /> },
          { path: "settings", element: <OrgSettings /> },
        ],
      },
    ],
  },
  { path: "*", element: <NotFound /> },
];
