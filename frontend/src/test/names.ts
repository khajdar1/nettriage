/** Letters and digits only: what a person sees and what they'd say, without spacing or punctuation. */
function plain(text: string): string {
  return text.replace(/[^\p{L}\p{N}]/gu, "").toLowerCase();
}

/** Whether a link's accessible name begins with its visible text (WCAG 2.5.3, label in name). */
export function namedAsShown(link: HTMLElement): boolean {
  const shown = plain(link.textContent ?? "");
  return plain(link.getAttribute("aria-label") ?? link.textContent ?? "").startsWith(shown);
}
