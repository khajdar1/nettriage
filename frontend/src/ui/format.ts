const DATE = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
const DATE_TIME = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const NUMBER = new Intl.NumberFormat(undefined);

/** A date as the person's browser writes dates, such as "3 Oct 2026". */
export function formatDate(iso: string): string {
  return DATE.format(new Date(iso));
}

/** A moment as the person's browser writes it, such as "3 Oct 2026, 14:05". */
export function formatDateTime(iso: string): string {
  return DATE_TIME.format(new Date(iso));
}

/** A count with the person's digit grouping, such as "1,204". */
export function formatNumber(value: number): string {
  return NUMBER.format(value);
}

/**
 * An AI cost in US dollars, from the API's decimal string. A call costs fractions of a cent, so
 * amounts under a dollar keep four decimals.
 */
export function formatUsd(amount: string): string {
  const value = Number(amount);
  if (!Number.isFinite(value) || value === 0) {
    return "$0.00";
  }
  if (value < 0.0001) {
    return "under $0.0001";
  }
  return `$${value.toFixed(value < 1 ? 4 : 2)}`;
}
