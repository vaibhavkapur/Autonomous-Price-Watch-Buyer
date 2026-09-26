"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import { api, money, PurchaseDetail, Watch } from "@/lib/api";

/** Purchase detail: final checkout, authorization result, order/payment status, receipt evidence (plan §20 view 4). */
export default function PurchasePage() {
  const { id } = useParams<{ id: string }>();
  const [watch, setWatch] = useState<Watch | null>(null);
  const [detail, setDetail] = useState<PurchaseDetail | null>(null);
  const [evidence, setEvidence] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [w, d] = await Promise.all([api<Watch>(`/v1/watches/${id}`), api<PurchaseDetail>(`/v1/watches/${id}/purchase`)]);
      setWatch(w);
      setDetail(d);
    } catch (e) {
      setError(String((e as Error).message));
    }
  }, [id]);

  useEffect(() => {
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [load]);

  async function reveal(artifactId: string) {
    const ev = await api<{ content: unknown }>(`/v1/watches/${id}/evidence/${artifactId}`);
    setEvidence((prev) => ({ ...prev, [artifactId]: ev.content }));
  }

  if (error) return <p className="error">{error}</p>;
  if (!watch || !detail) return <p className="muted">Loading…</p>;

  return (
    <>
      <h1>
        Purchase detail <StatusBadge value={watch.status} />
      </h1>
      <p className="muted">
        <Link href={`/watches/${id}`}>← back to watch</Link> · {watch.product_constraints.title}
      </p>

      {detail.attempts.length === 0 && <div className="panel muted">No purchase attempt yet. The watch has not found a qualifying, validated checkout.</div>}

      {detail.attempts.map((a) => (
        <div className="panel" key={a.id}>
          <h2>Attempt {a.id}</h2>
          <div className="grid">
            <table>
              <tbody>
                <tr>
                  <th>Claim</th>
                  <td>
                    <StatusBadge value={a.claim_state} />
                  </td>
                </tr>
                <tr>
                  <th>Order</th>
                  <td>
                    <StatusBadge value={a.order_state} /> {a.external_references.order_id && <code>{a.external_references.order_id}</code>}
                    {a.external_references.permalink_url && (
                      <div className="muted">{a.external_references.permalink_url}</div>
                    )}
                  </td>
                </tr>
                <tr>
                  <th>Payment</th>
                  <td>
                    <StatusBadge value={a.payment_state} /> {a.external_references.payment_id && <code>{a.external_references.payment_id}</code>}
                  </td>
                </tr>
                <tr>
                  <th>Final checkout</th>
                  <td>
                    {a.merchant_id} · <code>{a.checkout_id}</code> · delivered total <strong>{money(a.final_total_minor, a.currency)}</strong>
                  </td>
                </tr>
                <tr>
                  <th>Idempotency key</th>
                  <td>
                    <code>{a.idempotency_key}</code>
                  </td>
                </tr>
                <tr>
                  <th>Timing</th>
                  <td className="muted">
                    claimed {a.claimed_at} · submitted {a.submitted_at ?? "—"} · resolved {a.resolved_at ?? "—"}
                  </td>
                </tr>
                {a.last_error && (
                  <tr>
                    <th>Note</th>
                    <td className="error">{a.last_error}</td>
                  </tr>
                )}
              </tbody>
            </table>
            <div>
              <h2>Outbound events</h2>
              <table>
                <tbody>
                  {(detail.outbound_events[a.id] ?? []).map((o) => (
                    <tr key={o.id}>
                      <td>
                        <code>{o.kind}</code>
                      </td>
                      <td>
                        <StatusBadge value={o.state} />
                      </td>
                      <td className="muted">digest {o.payload_digest.slice(0, 12)}…</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {detail.reconciliation_cases
                .filter((c) => c.attempt_id === a.id)
                .map((c) => (
                  <p key={c.id} className={c.state === "open" ? "error" : "muted"}>
                    Reconciliation case {c.state} ({c.checks} check(s)){c.resolution ? ` → ${c.resolution}` : ""}: {c.summary}
                  </p>
                ))}
            </div>
          </div>
        </div>
      ))}

      <div className="grid">
        <div className="panel">
          <h2>Authorization result</h2>
          <table>
            <thead>
              <tr>
                <th>Profile</th>
                <th>Status</th>
                <th>Presentation</th>
                <th>Expires</th>
              </tr>
            </thead>
            <tbody>
              {detail.authorizations.map((au) => (
                <tr key={au.id}>
                  <td>{au.profile_version}</td>
                  <td>
                    <StatusBadge value={au.status} />
                  </td>
                  <td>
                    <StatusBadge value={au.presentation_state} />
                  </td>
                  <td className="muted">{au.expires_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="panel">
          <h2>Notifications</h2>
          {detail.notifications.length === 0 && <p className="muted">None.</p>}
          {detail.notifications.map((n) => (
            <p key={n.id}>
              <strong>{n.subject}</strong> <span className="muted">({n.state})</span>
              <br />
              {n.body}
            </p>
          ))}
        </div>
      </div>

      <div className="panel">
        <h2>Evidence bundle (protected native artifacts; credentials redacted)</h2>
        <table>
          <thead>
            <tr>
              <th>Kind</th>
              <th>Digest</th>
              <th>Created</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {detail.evidence.map((ev) => (
              <tr key={ev.artifact_id}>
                <td>
                  <code>{ev.kind}</code>
                </td>
                <td className="muted">{ev.digest}</td>
                <td className="muted">{ev.created_at}</td>
                <td>
                  {evidence[ev.artifact_id] ? (
                    <details open>
                      <summary>hide</summary>
                      <pre>{JSON.stringify(evidence[ev.artifact_id], null, 2)}</pre>
                    </details>
                  ) : (
                    <button className="secondary" onClick={() => reveal(ev.artifact_id)}>
                      inspect
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
