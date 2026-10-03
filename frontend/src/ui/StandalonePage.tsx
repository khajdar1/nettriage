import type { ReactNode } from "react";
import { usePageTitle } from "./usePageTitle";
import { Wordmark } from "./Wordmark";

/** A page outside the app's frame (not found, an invitation, signing in): the mark, then content. */
export function StandaloneFrame({ children }: { children: ReactNode }) {
  return (
    <div className="site standalone">
      <header className="site-top">
        <Wordmark to="/" />
      </header>
      <main id="main" className="page narrow">
        {children}
      </main>
    </div>
  );
}

/** A standalone page with its heading, which also names the browser tab. */
export function StandalonePage({ title, children }: { title: string; children: ReactNode }) {
  usePageTitle(title);
  return (
    <StandaloneFrame>
      <h1>{title}</h1>
      {children}
    </StandaloneFrame>
  );
}
