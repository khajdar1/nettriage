import { Link, useSearchParams } from "react-router";
import { signInUrl, useMe } from "../auth/session";
import { ExternalLink } from "../ui/ExternalLink";
import { usePageTitle } from "../ui/usePageTitle";

export const SOURCE_URL = "https://github.com/khajdar1/nettriage";

// The API sends a failed sign-in back here as `/?sign_in=<reason>` (spec §4.1, §6.5).
const SIGN_IN_PROBLEMS: Record<string, string> = {
  expired: "Your sign-in took too long, or started in another browser. Please sign in again.",
  failed: "Sign-in was cancelled or didn't finish. Please try again.",
  disabled: "This account is disabled.",
  unavailable: "Sign-in isn't available right now. Please try again in a few minutes.",
  limited: "Too many sign-in attempts. Please wait a minute, then try again.",
};

const FEATURES = [
  {
    title: "Detect",
    text: "Rules find port scans, SSH and RDP brute force, and unusual outbound volume in AWS VPC Flow Logs.",
  },
  {
    title: "Explain",
    text: "An AI explains each finding from its data alone, checked against that data and labeled as AI-generated.",
  },
  {
    title: "Triage",
    text: "Your team sets status and owner and adds comments, and nobody silently overwrites anybody else.",
  },
];

export function Landing() {
  usePageTitle(null);
  const [params] = useSearchParams();
  const problem = SIGN_IN_PROBLEMS[params.get("sign_in") ?? ""];
  const me = useMe();
  return (
    <main className="landing">
      <section className="hero">
        <p className="eyebrow">Network threat triage</p>
        <h1>NetTriage</h1>
        <p className="lede">
          Upload AWS VPC Flow Logs and get findings you can act on: detected by rules, explained by
          AI, triaged by your team.
        </p>
        {problem !== undefined && (
          <p role="alert" className="notice notice-error">
            {problem}
          </p>
        )}
        <div className="actions">
          {me.data ? (
            <Link className="button button-primary" to="/app">
              Open your organizations
            </Link>
          ) : (
            <a className="button button-primary" href={signInUrl("/app")}>
              Sign in / Sign up
            </a>
          )}
          <ExternalLink className="button" href={SOURCE_URL}>
            Source on GitHub
          </ExternalLink>
        </div>
      </section>
      <section className="features" aria-label="What it does">
        {FEATURES.map((feature) => (
          <article key={feature.title} className="feature">
            <h2>{feature.title}</h2>
            <p>{feature.text}</p>
          </article>
        ))}
      </section>
    </main>
  );
}
