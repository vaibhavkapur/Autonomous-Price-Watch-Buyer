"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import { api, Event, money, Observation, Watch } from "@/lib/api";

export default function WatchDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [watch, setWatch] = useState<Watch | null>(null);
  const [observations, setObservations] = useState<Observation[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [demo, setDemo] = useState({ merchant: "merchant_a", sku: "K1-BLK-US", item: "89.00", shipping: "12.00", tax: "4.00" });
  const [demoMsg, setDemoMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [w, o, t] = await Promise.all([
        api<Watch>(`/v1/watches/${id}`),
        api<{ observations: Observation[] }>(`/v1/watches/${id}/observations`),
        api<{ events: Event[] }>(`/v1/watches/${id}/timeline`),
      ]);
      setWatch(w);
      setObservations(o.observations);
      setEvents(t.events);
      setError(null);
    } catch (e) {
      setError(String((e as Error).message));
    }
  }, [id]);

  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load]);

  async function mutate(action: "pause" | "resume" | "cancel") {
    if (!watch) return;
    try {
      await api(`/v1/watches/${id}/${action}`, { method: "POST", mutation: true, ifMatch: watch.version, body: JSON.stringify({ expected_version: watch.version }) });
      await load();
    } catch (e) {
      setError(JSON.stringify((e as { body?: unknown }).body ?? (e as Error).message));
    }
  }

  const cents = (s: string) => Math.round(parseFloat(s) * 100);

  async function applyScenario() {
    setDemoMsg(null);
    try {
      await api(`/demo/merchants/${demo.merchant}/price-scenario`, {
        method: "POST",
        body: JSON.stringify({ sku: demo.sku, item_price_minor: cents(demo.item), shipping_minor: cents(demo.shipping), tax_minor: cents(demo.tax) }),
      });
      setDemoMsg(`Scenario applied: delivered total would be ${money(cents(demo.item) + cents(demo.shipping) + cents(demo.tax))}`);
    } catch (e) {
      setDemoMsg(String((e as Error).message));
    }
  }

  async function evaluateNow() {
    setDemoMsg(null);
    try {
      const r = await api<{ results?: { outcome: string; reason?: string }[]; note?: string }>(`/demo/watches/${id}/evaluate-now`, { method: "POST" });
      setDemoMsg(r.results ? r.results.map((x) => `${x.outcome}${x.reason ? `: ${x.reason}` : ""}`).join("; ") : r.note ?? "scheduled");
      await load();
    } catch (e) {
      setDemoMsg(String((e as Error).message));
    }
  }

  async function advance(seconds: number) {
    try {
      await api(`/demo/clock/advance?seconds=${seconds}`, { method: "POST" });
      await load();
    } catch (e) {
      setDemoMsg(String((e as Error).message));
    }
  }

  if (error && !watch) return <p className="error">{error}</p>;
  if (!watch) return <p className="muted">Loading…</p>;

  const rejected = observations.filter((o) => o.eligibility !== "eligible");
  const live = observations.filter((o) => o.source_reference.startsWith("merchant_") || o.source_reference.startsWith("ucp_checkout")).slice(0, 12);

  return (
    <>
      <h1>
        {watch.product_constraints.title} <StatusBadge value={watch.status} />
      </h1>
      <p className="muted">
        {watch.id} · version {watch.version} · profile {watch.authorization_profile.toUpperCase()} · <Link href={`/watches/${id}/purchase`}>purchase detail</Link>
      </p>
      {error && <p className="error">{error}</p>}

      <div className="grid">
        <div className="panel">
          <h2>Rule</h2>
          <p>
            Delivered total {watch.price_operator === "lt" ? "strictly below" : "at most"} <strong>{watch.threshold_display}</strong>
            <br />
            <span className="muted">largest qualifying total {money(watch.inclusive_ceiling_minor, watch.currency)}</span>
          </p>
          <p>
            Merchants:{" "}
            {watch.allowed_merchants.map((m) => (
              <span key={m}>
                {m} <code>{watch.merchant_sku_mapping[m]}</code>{" "}
              </span>
            ))}
          </p>
          <p>
            Deadline {watch.expires_at} ({watch.timezone}) · next check {watch.next_check_at ?? "—"}
          </p>
          <div className="row">
            {watch.status === "watching" && (
              <button className="secondary" onClick={() => mutate("pause")}>
                Pause
              </button>
            )}
            {watch.status === "paused" && (
              <button className="secondary" onClick={() => mutate("resume")}>
                Resume
              </button>
            )}
            {(watch.status === "awaiting_authorization" || watch.status === "draft" || watch.status === "paused") && (
              <Link href={`/watches/${id}/authorize`}>
                <button>Review authorization</button>
              </Link>
            )}
            {!["cancelled", "expired", "purchased", "resolved_not_purchased"].includes(watch.status) && (
              <button className="danger" onClick={() => mutate("cancel")}>
                Cancel
              </button>
            )}
          </div>
          {watch.cancel_requested && watch.status !== "cancelled" && (
            <p className="error">Cancellation requested after submission: future activity stops, but an order already submitted to the merchant may not be cancellable.</p>
          )}
        </div>

        <div className="panel">
          <h2>Authorization</h2>
          {watch.authorization ? (
            <table>
              <tbody>
                <tr>
                  <th>Profile</th>
                  <td>{watch.authorization.profile_version}</td>
                </tr>
                <tr>
                  <th>Status</th>
                  <td>
                    <StatusBadge value={watch.authorization.status} /> presentation <StatusBadge value={watch.authorization.presentation_state} />
                  </td>
                </tr>
                <tr>
                  <th>Expires</th>
                  <td>{watch.authorization.expires_at}</td>
                </tr>
                <tr>
                  <th>Agent key</th>
                  <td>
                    <code>{watch.authorization.agent_key_id}</code>
                  </td>
                </tr>
              </tbody>
            </table>
          ) : (
            <p className="muted">No active authorization.</p>
          )}
        </div>

        <div className="panel">
          <h2>Demo controls {watch.clock?.simulated && <span className="badge sim">simulated clock</span>}</h2>
          <div className="row">
            <select value={demo.merchant} onChange={(e) => setDemo({ ...demo, merchant: e.target.value, sku: watch.merchant_sku_mapping[e.target.value] ?? demo.sku })} style={{ width: 140 }}>
              {watch.allowed_merchants.map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
            <input style={{ width: 90 }} value={demo.item} onChange={(e) => setDemo({ ...demo, item: e.target.value })} title="item" />
            <input style={{ width: 90 }} value={demo.shipping} onChange={(e) => setDemo({ ...demo, shipping: e.target.value })} title="shipping" />
            <input style={{ width: 90 }} value={demo.tax} onChange={(e) => setDemo({ ...demo, tax: e.target.value })} title="tax" />
            <button className="secondary" onClick={applyScenario}>
              Set item / shipping / tax
            </button>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button onClick={evaluateNow}>Evaluate now</button>
            <button className="secondary" onClick={() => advance(3600)}>
              +1 h
            </button>
            <button className="secondary" onClick={() => advance(86400)}>
              +1 day
            </button>
          </div>
          {demoMsg && <p className="muted">{demoMsg}</p>}
        </div>
      </div>

      <div className="panel">
        <h2>Recent offers and total-price breakdowns</h2>
        <table>
          <thead>
            <tr>
              <th>Observed</th>
              <th>Merchant</th>
              <th>Source</th>
              <th>SKU</th>
              <th>Item</th>
              <th>Shipping</th>
              <th>Tax</th>
              <th>Fees</th>
              <th>Delivered total</th>
              <th>Verdict</th>
              <th>Reasons</th>
            </tr>
          </thead>
          <tbody>
            {live.map((o) => (
              <tr key={o.id}>
                <td className="muted">{o.observed_at.slice(11, 19)}</td>
                <td>{o.merchant_id}</td>
                <td className="muted">{o.source_reference.startsWith("ucp_checkout") ? "UCP checkout" : "feed"}</td>
                <td>
                  <code>{o.sku}</code>
                </td>
                <td>{money(o.price_components.item_subtotal)}</td>
                <td>{money(o.price_components.shipping)}</td>
                <td>{o.price_components.tax === null ? <span className="error">unknown</span> : money(o.price_components.tax)}</td>
                <td>{money(o.price_components.fees)}</td>
                <td>
                  <strong>{money(o.total_minor)}</strong>
                </td>
                <td>
                  <StatusBadge value={o.eligibility} />
                </td>
                <td className="muted">{o.reasons.join("; ")}</td>
              </tr>
            ))}
            {live.length === 0 && (
              <tr>
                <td colSpan={11} className="muted">
                  No observations yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        {rejected.length > 0 && (
          <p className="muted">
            {rejected.length} rejected / incomplete candidate(s). Example: an advertised {money(rejected[0].price_components.item_subtotal)} item was rejected because its delivered cost was {money(rejected[0].total_minor)} ({rejected[0].reasons[0]}).
          </p>
        )}
      </div>

      <div className="panel">
        <h2>Decision history</h2>
        <table>
          <thead>
            <tr>
              <th>At</th>
              <th>Event</th>
              <th>Actor</th>
              <th>v</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {[...events].reverse().map((e) => (
              <tr key={e.id}>
                <td className="muted">{e.at.replace("T", " ").slice(0, 19)}</td>
                <td>
                  <code>{e.type}</code>
                </td>
                <td className="muted">{e.actor}</td>
                <td className="muted">{e.watch_version ?? ""}</td>
                <td>
                  <details>
                    <summary className="muted">{summarize(e.details)}</summary>
                    <pre>{JSON.stringify(e.details, null, 2)}</pre>
                  </details>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function summarize(d: Record<string, unknown>): string {
  const keys = ["reason", "outcome", "total_minor", "order_id", "merchant_id", "checkout_id", "ok"];
  const parts = keys.filter((k) => d[k] !== undefined).map((k) => `${k}=${String(d[k])}`);
  return parts.join(" ") || Object.keys(d).slice(0, 4).join(", ");
}
