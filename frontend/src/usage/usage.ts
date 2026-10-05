/** An organization's AI usage by UTC day (spec §7): calls, tokens and cost, with totals. */
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Usage = components["schemas"]["UsageOut"];

export const PERIODS = [7, 30, 90] as const;
const DAY_MS = 86_400_000;

/** The period in an address such as `?days=7`: 7, 30 or 90 days, and 30 otherwise. */
export function periodFrom(value: string | null): number {
  const days = Number(value);
  return PERIODS.some((period) => period === days) ? days : 30;
}

/** The last `days` UTC days, oldest first, ending today, as `YYYY-MM-DD`. */
export function lastDays(days: number, now: number): string[] {
  const today = Math.floor(now / DAY_MS) * DAY_MS;
  return Array.from({ length: days }, (_, index) =>
    new Date(today - (days - 1 - index) * DAY_MS).toISOString().slice(0, 10),
  );
}

/** Every day of the period with its cost, oldest first; the API lists only days with calls. */
export function dailyCost(
  usage: Usage,
  days: number,
  now: number,
): { day: string; cost: number }[] {
  const costs = new Map(usage.days.map((day) => [day.day, Number(day.cost_usd)]));
  return lastDays(days, now).map((day) => ({ day, cost: costs.get(day) ?? 0 }));
}

export function useUsage(orgId: string, days: number, enabled: boolean) {
  return useQuery({
    queryKey: ["usage", orgId, days],
    enabled,
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/usage", {
          params: { path: { org_id: orgId }, query: { days } },
        }),
      ),
  });
}
