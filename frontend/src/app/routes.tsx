import type { RouteObject } from "react-router";
import { Landing } from "../pages/Landing";
import { NotFound } from "../pages/NotFound";

/** Every page of the app (spec §10). CloudFront serves index.html for each of these paths. */
export const routes: RouteObject[] = [
  { path: "/", element: <Landing /> },
  { path: "*", element: <NotFound /> },
];
