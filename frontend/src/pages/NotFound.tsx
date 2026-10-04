import { Link } from "react-router";
import { StandalonePage } from "../ui/StandalonePage";

export function NotFound() {
  return (
    <StandalonePage title="Page not found">
      <p>There's nothing at this address.</p>
      <Link to="/">Go to the home page</Link>
    </StandalonePage>
  );
}
