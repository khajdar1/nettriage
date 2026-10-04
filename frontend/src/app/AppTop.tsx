import type { ReactNode } from "react";
import { Wordmark } from "../ui/Wordmark";

/** The signed-in header: the mark, in the pages' column, and whatever sits on its right. */
export function AppTop({ children }: { children?: ReactNode }) {
  return (
    <header className="app-top">
      <div className="app-top-inner">
        <Wordmark to="/app" />
        {children}
      </div>
    </header>
  );
}
