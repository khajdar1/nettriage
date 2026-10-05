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

const TIME = new Intl.DateTimeFormat(undefined, { timeStyle: "short" });
const CLOCK = new Intl.DateTimeFormat(undefined, { timeStyle: "medium" });

/** A time of day, such as "14:05", for the end of a window that starts the same day. */
export function formatTime(iso: string): string {
  return TIME.format(new Date(iso));
}

/** A time of day to the second, such as "14:05:09", for flows minutes apart. */
export function formatClock(iso: string): string {
  return CLOCK.format(new Date(iso));
}

const UTC_DAY = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" });

/** A UTC day such as "2026-10-04", written as a date that never shifts with the time zone. */
export function formatDay(day: string): string {
  return UTC_DAY.format(new Date(`${day}T00:00:00Z`));
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * How long ago a moment was (Plan 6d): "just now", "5 min ago", "2 h ago", "yesterday",
 * "3 days ago", then its date from a week on. A moment slightly ahead of this clock is "just now".
 */
export function formatAgo(iso: string, now: number): string {
  const elapsed = now - Date.parse(iso);
  if (elapsed < MINUTE) {
    return "just now";
  }
  if (elapsed < HOUR) {
    return `${Math.floor(elapsed / MINUTE)} min ago`;
  }
  if (elapsed < DAY) {
    return `${Math.floor(elapsed / HOUR)} h ago`;
  }
  if (elapsed < 2 * DAY) {
    return "yesterday";
  }
  if (elapsed < 7 * DAY) {
    return `${Math.floor(elapsed / DAY)} days ago`;
  }
  return formatDate(iso);
}
