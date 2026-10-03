import type { RouteObject } from "react-router";
import { Landing } from "../pages/Landing";
import { NotFound } from "../pages/NotFound";
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
    ],
  },
  { path: "*", element: <NotFound /> },
];
