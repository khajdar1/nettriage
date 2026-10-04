import type { ReactNode } from "react";
import { usePageTitle } from "./usePageTitle";
import { Wordmark } from "./Wordmark";

/**
 * A page outside the app's frame (not found, an invitation, signing in): the mark, then its
 * heading, which also names the browser tab, then content.
 */
export function StandalonePage({ title, children }: { title: string; children: ReactNode }) {
  usePageTitle(title);
  return (
    <div className="site standalone">
      <header className="site-top">
        <Wordmark to="/" />
      </header>
      <main id="main" className="page narrow">
        <h1>{title}</h1>
        {children}
      </main>
    </div>
  );
}
