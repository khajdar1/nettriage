import { useSearchParams } from "react-router";
import { useOrg } from "../../orgs/org";
import { canReadUsage } from "../../orgs/permissions";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatDay, formatNumber, formatUsd } from "../../ui/format";
import { Loading } from "../../ui/Loading";
import { usePageTitle } from "../../ui/usePageTitle";
import { PERIODS, dailyCost, periodFrom, useUsage } from "../../usage/usage";
import { UsageChart } from "./UsageChart";

/** `/app/orgs/:org/usage`: what the AI did and cost, by UTC day, for Owners and Admins. */
export function Usage() {
  const org = useOrg();
  usePageTitle(`AI usage · ${org.name}`);
  const [search, setSearch] = useSearchParams();
  const days = periodFrom(search.get("days"));
  const allowed = canReadUsage(org.role);
  const usage = useUsage(org.id, days, allowed);
  return (
    <main id="main" className="page">
      <h1>AI usage</h1>
      {!allowed && <p>Only Owners and Admins can see the AI usage.</p>}
      {allowed && (
        <div className="filters">
          <div className="field">
            <label htmlFor="usage-period">Period</label>
            <select
              id="usage-period"
              value={days}
              onChange={(event) => setSearch({ days: event.target.value })}
            >
              {PERIODS.map((period) => (
                <option key={period} value={period}>
                  Last {period} days
                </option>
              ))}
            </select>
          </div>
        </div>
      )}
      {usage.isLoading && <Loading />}
      {usage.isError && <ErrorNotice error={usage.error} />}
      {usage.data !== undefined && usage.data.totals.calls === 0 && (
        <p className="muted">No AI calls in the last {days} days.</p>
      )}
      {usage.data !== undefined && usage.data.totals.calls > 0 && (
        <>
          <UsageChart series={dailyCost(usage.data, days, Date.now())} />
          <table className="table">
            <caption className="visually-hidden">AI calls by day, newest first</caption>
            <thead>
              <tr>
                <th scope="col">Day (UTC)</th>
                <th scope="col">Calls</th>
                <th scope="col">Input tokens</th>
                <th scope="col">Output tokens</th>
                <th scope="col">Cost</th>
              </tr>
            </thead>
            <tbody>
              {usage.data.days.map((day) => (
                <tr key={day.day}>
                  <td className="date">{formatDay(day.day)}</td>
                  <td>{formatNumber(day.calls)}</td>
                  <td>{formatNumber(day.input_tokens)}</td>
                  <td>{formatNumber(day.output_tokens)}</td>
                  <td>{formatUsd(day.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th scope="row">Total</th>
                <td>{formatNumber(usage.data.totals.calls)}</td>
                <td>{formatNumber(usage.data.totals.input_tokens)}</td>
                <td>{formatNumber(usage.data.totals.output_tokens)}</td>
                <td>{formatUsd(usage.data.totals.cost_usd)}</td>
              </tr>
            </tfoot>
          </table>
        </>
      )}
    </main>
  );
}
