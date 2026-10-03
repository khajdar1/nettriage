import { Link } from "react-router";
import { usePageTitle } from "../ui/usePageTitle";

export function NotFound() {
  usePageTitle("Page not found");
  return (
    <main className="page narrow">
      <h1>Page not found</h1>
      <p>There's nothing at this address.</p>
      <Link to="/">Go to the home page</Link>
    </main>
  );
}
