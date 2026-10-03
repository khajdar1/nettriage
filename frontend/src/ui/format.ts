const DATE = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

/** A date as the person's browser writes dates, such as "3 Oct 2026". */
export function formatDate(iso: string): string {
  return DATE.format(new Date(iso));
}
