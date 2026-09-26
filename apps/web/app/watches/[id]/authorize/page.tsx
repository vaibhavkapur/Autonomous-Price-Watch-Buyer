"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import { api, money, Proposal, Watch } from "@/lib/api";

/** Authorization review: the actual conditions being delegated (plan §20 view 2). */
export default function AuthorizePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [watch, setWatch] = useState<Watch | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const p = await api<Proposal>(`/v1/watches/${id}/authorization-proposal`, { method: "POST", mutation: true });
        setProposal(p);
        setWatch(await api<Watch>(`/v1/watches/${id}`));
      } catch (e) {
        setError(String((e as Error).message));
      }
    })();
  }, [id]);

  async function approve() {
    if (!watch) return;
    setBusy(true);
    setError(null);
    try {
      await api<Watch>(`/v1/watches/${id}/activate`, { method: "POST", mutation: true, ifMatch: watch.version, body: JSON.stringify({ expected_version: watch.version }) });
      router.push(`/watches/${id}`);
    } catch (e) {
      setError(JSON.stringify((e as { body?: unknown }).body ?? (e as Error).message));
      setBusy(false);
    }
  }

  if (error && !proposal) return <p className="error">{error}</p>;
  if (!proposal || !watch) return <p className="muted">Preparing the proposal…</p>;

  return (
    <>
      <h1>Review authorization</h1>
      <p className="muted">
        Watch <Link href={`/watches/${id}`}>{id}</Link> · <StatusBadge value={watch.status} /> · version {watch.version}
      </p>

      <div className="panel">
        <h2>You are delegating exactly this</h2>
        <ul className="sentences">
          {proposal.rule.sentences.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
        {watch.original_request && (
          <p className="muted">
            Your wording (context only, not the executable rule): “{watch.original_request}”
          </p>
        )}
      </div>

      <div className="grid">
        <div className="panel">
          <h2>Protocol constraints ({proposal.profile_version})</h2>
          <table>
            <tbody>
              <tr>
                <th>Acceptable items</th>
                <td>
                  {proposal.delegation.acceptable_items.map((i) => (
                    <div key={i.id}>
                      <code>{i.id}</code> — {i.title} <span className="muted">@ {i.merchant_id}</span>
                    </div>
                  ))}
                </td>
              </tr>
              <tr>
                <th>Allowed merchants / payees</th>
                <td>
                  {proposal.delegation.merchants.map((m) => (
                    <div key={m.id}>
                      {m.name} <span className="muted">{m.website}</span>
                    </div>
                  ))}
                </td>
              </tr>
              <tr>
                <th>Quantity</th>
                <td>{proposal.delegation.quantity}</td>
              </tr>
              <tr>
                <th>Amount range</th>
                <td>
                  {proposal.delegation.currency} 0 … <strong>{money(proposal.delegation.inclusive_max_minor, proposal.delegation.currency)}</strong> (inclusive maximum, {proposal.delegation.inclusive_max_minor} minor units)
                </td>
              </tr>
              <tr>
                <th>Authority ends</th>
                <td>{proposal.delegation.not_after}</td>
              </tr>
              <tr>
                <th>Payment instrument</th>
                <td>{proposal.delegation.payment_instrument.description}</td>
              </tr>
              <tr>
                <th>Agent key bound (cnf)</th>
                <td>
                  <code>{proposal.delegation.agent_key_id}</code>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div className="panel">
          <h2>Executable rule</h2>
          <pre>{JSON.stringify(proposal.rule.executable_rule, null, 2)}</pre>
          {proposal.rule.unknowns.length > 0 && (
            <>
              <h2>Blocking unknowns</h2>
              <ul className="sentences">
                {proposal.rule.unknowns.map((u) => (
                  <li key={u} className="error">
                    {u}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>

      <div className="panel row">
        <button onClick={approve} disabled={busy || !proposal.can_activate || watch.status !== "awaiting_authorization"}>
          Approve and start watching
        </button>
        <Link href={`/watches/${id}`} className="muted">
          Not now
        </Link>
        {error && <span className="error">{error}</span>}
      </div>
      <p className="muted">
        Approval signs the open mandates on the Trusted Surface (deterministic server code holding the signing key, not the model). Changing product, price, merchants, deadline or destination requires a new review.
      </p>
    </>
  );
}
