"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import { api, money, Watch } from "@/lib/api";

export default function WatchesPage() {
  const [watches, setWatches] = useState<Watch[] | null>(null);
  const [metrics, setMetrics] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      Promise.all([api<{ watches: Watch[] }>("/v1/watches"), api<{ metrics: Record<string, number> }>("/v1/metrics")])
        .then(([w, m]) => {
          setWatches(w.watches);
          setMetrics(m.metrics);
          setError(null);
        })
        .catch((e) => setError(String(e.message)));
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, []);

  return (
    <>
      <h1>Watches</h1>
      {error && <p className="error">{error}</p>}
      <div className="grid">
        {[
          ["observations", "Observations"],
          ["qualifying_offers", "Qualifying offers"],
          ["purchase_attempts", "Purchase attempts"],
          ["duplicate_claims_prevented", "Duplicate claims prevented"],
          ["open_reconciliation_cases", "Unresolved executions"],
          ["purchases_completed", "Purchases"],
        ].map(([k, label]) => (
          <div className="panel" key={k}>
            <div className="muted">{label}</div>
            <div className="metric">{metrics[k] ?? 0}</div>
          </div>
        ))}
      </div>
      <div className="panel">
        {watches === null ? (
          <p className="muted">Loading…</p>
        ) : watches.length === 0 ? (
          <p className="muted">
            No watches yet. <Link href="/watches/new">Create one</Link>.
          </p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Product</th>
                <th>Rule</th>
                <th>Merchants</th>
                <th>Deadline</th>
                <th>Profile</th>
                <th>Status</th>
                <th>Next check</th>
              </tr>
            </thead>
            <tbody>
              {watches.map((w) => (
                <tr key={w.id}>
                  <td>
                    <Link href={`/watches/${w.id}`}>{w.product_constraints.title}</Link>
                  </td>
                  <td>
                    delivered total {w.price_operator === "lt" ? "<" : "≤"} {w.threshold_display} <span className="muted">(max {money(w.inclusive_ceiling_minor, w.currency)})</span>
                  </td>
                  <td>{w.allowed_merchants.join(", ")}</td>
                  <td>{w.expires_at}</td>
                  <td>{w.authorization_profile.toUpperCase()}</td>
                  <td>
                    <StatusBadge value={w.status} />
                  </td>
                  <td className="muted">{w.next_check_at ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
