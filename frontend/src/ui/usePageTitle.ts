import { useLayoutEffect } from "react";

/**
 * Names the page in the browser tab and for screen readers: "Members · NetTriage". Set as the
 * page is put on screen, so the title never lags behind the content.
 */
export function usePageTitle(title: string | null): void {
  useLayoutEffect(() => {
    document.title = title === null ? "NetTriage" : `${title} · NetTriage`;
  }, [title]);
}
