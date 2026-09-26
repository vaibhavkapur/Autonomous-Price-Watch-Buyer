"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { api, Catalog, money, Watch } from "@/lib/api";

interface Parsed {
  price_rule?: { operator: "lt" | "lte"; delivered_total_minor: number };
  expires_at?: string;
  allowed_merchants?: string[];
  notes: string[];
  unknowns: string[];
}

interface Preview {
  inclusive_ceiling_minor: number;
  sentences: string[];
  unknowns: string[];
}

export default function NewWatchPage() {
  const router = useRouter();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [destinations, setDestinations] = useState<{ id: string; label: string }[]>([]);
  const [text, setText] = useState("Buy this exact keyboard if its delivered price drops below $100 before Sunday. Use only these two merchants and buy it once.");
  const [parsed, setParsed] = useState<Parsed | null>(null);
  const [productId, setProductId] = useState("");
  const [operator, setOperator] = useState<"lt" | "lte">("lt");
  const [threshold, setThreshold] = useState("100.00");
  const [merchants, setMerchants] = useState<string[]>([]);
  const [skus, setSkus] = useState<Record<string, string>>({});
  const [expiresAt, setExpiresAt] = useState("");
  const [timezone, setTimezone] = useState("Asia/Kolkata");
  const [destination, setDestination] = useState("");
  const [profile, setProfile] = useState<"ap2" | "vi">("ap2");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Catalog>("/v1/catalog").then((c) => {
      setCatalog(c);
      if (c.products[0]) setProductId(c.products[0].product_id);
    });
    api<{ destinations: { id: string; label: string }[] }>("/v1/destinations").then((d) => {
      setDestinations(d.destinations);
      if (d.destinations[0]) setDestination(d.destinations[0].id);
    });
  }, []);

  const product = useMemo(() => catalog?.products.find((p) => p.product_id === productId), [catalog, productId]);

  // Machine-verifiable SKU candidates for the selected product, per merchant.
  const candidates = useMemo(() => {
    const out: Record<string, { sku: string; title: string; exact: boolean }[]> = {};
    if (!catalog || !product) return out;
    for (const m of catalog.merchants) {
      out[m.id] = m.items
        .filter((i) => i.canonical_product_id === product.product_id)
        .map((i) => ({
          sku: i.sku,
          title: i.title,
          exact: !i.bundle && Object.entries(product.attributes).every(([k, v]) => i.attributes[k] === v),
        }));
    }
    return out;
  }, [catalog, product]);

  const thresholdMinor = useMemo(() => {
    const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(threshold.trim().replace("$", ""));
    if (!m) return null;
    return parseInt(m[1], 10) * 100 + parseInt((m[2] ?? "0").padEnd(2, "0"), 10);
  }, [threshold]);

  const draft = () => ({
    original_request: text,
    product: product ? { product_id: product.product_id, title: product.title, attributes: product.attributes, bundle: product.bundle } : null,
    merchant_skus: Object.fromEntries(merchants.map((m) => [m, skus[m]])),
    quantity: 1,
    currency: "USD",
    price_rule: { operator, delivered_total_minor: thresholdMinor },
    allowed_merchants: merchants,
    destination_id: destination || null,
    expires_at: expiresAt,
    timezone,
    max_purchases: 1,
    authorization_profile: profile,
    poll_interval_seconds: 60,
  });

  async function parse() {
    setError(null);
    try {
      const p = await api<Parsed>("/v1/watches/parse", { method: "POST", body: JSON.stringify({ text, timezone }) });
      setParsed(p);
      if (p.price_rule) {
        setOperator(p.price_rule.operator);
        setThreshold(money(p.price_rule.delivered_total_minor).replace("USD ", ""));
      }
      if (p.expires_at) setExpiresAt(p.expires_at);
      if (p.allowed_merchants) setMerchants(p.allowed_merchants);
    } catch (e) {
      setError(String((e as Error).message));
    }
  }

  async function doPreview() {
    setError(null);
    try {
      setPreview(await api<Preview>("/v1/watches/preview", { method: "POST", body: JSON.stringify(draft()) }));
    } catch (e) {
      setError(JSON.stringify((e as { body?: unknown }).body ?? (e as Error).message));
    }
  }

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const w = await api<Watch>("/v1/watches", { method: "POST", mutation: true, body: JSON.stringify(draft()) });
      router.push(`/watches/${w.id}/authorize`);
    } catch (e) {
      setError(JSON.stringify((e as { body?: unknown }).body ?? (e as Error).message));
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Create watch</h1>
      <p className="muted">Describe what you want; then confirm the exact product, threshold, merchant scope and deadline. Only the reviewed fields become executable authority.</p>

      <div className="panel">
        <label>Instruction</label>
        <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} />
        <div className="row" style={{ marginTop: 8 }}>
          <button className="secondary" onClick={parse}>
            Propose fields from text
          </button>
          {parsed && (
            <span className="muted">
              {parsed.notes.length} note(s), {parsed.unknowns.length} unknown(s)
            </span>
          )}
        </div>
        {parsed && (
          <ul className="sentences" style={{ marginTop: 8 }}>
            {parsed.notes.map((n) => (
              <li key={n} className="muted">
                {n}
              </li>
            ))}
            {parsed.unknowns.map((u) => (
              <li key={u} className="error">
                {u}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="grid">
        <div className="panel">
          <h2>Exact product</h2>
          <label>Canonical product</label>
          <select value={productId} onChange={(e) => setProductId(e.target.value)}>
            {catalog?.products.map((p) => (
              <option key={p.product_id} value={p.product_id}>
                {p.title}
              </option>
            ))}
          </select>
          {product && (
            <p className="muted">
              {Object.entries(product.attributes)
                .map(([k, v]) => `${k}=${v}`)
                .join(", ")}
            </p>
          )}
          <label>Merchant scope and SKU mapping</label>
          {catalog?.merchants.map((m) => (
            <div key={m.id} className="row" style={{ marginBottom: 6 }}>
              <input
                type="checkbox"
                style={{ width: "auto" }}
                checked={merchants.includes(m.id)}
                onChange={(e) => setMerchants(e.target.checked ? [...merchants, m.id] : merchants.filter((x) => x !== m.id))}
              />
              <span style={{ minWidth: 140 }}>{m.name}</span>
              <select value={skus[m.id] ?? ""} onChange={(e) => setSkus({ ...skus, [m.id]: e.target.value })} disabled={!merchants.includes(m.id)}>
                <option value="">— select SKU —</option>
                {(candidates[m.id] ?? []).map((c) => (
                  <option key={c.sku} value={c.sku}>
                    {c.sku} — {c.title} {c.exact ? "" : "(NOT an exact match: rejected at creation)"}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>

        <div className="panel">
          <h2>Delivered-price rule</h2>
          <label>Comparison</label>
          <select value={operator} onChange={(e) => setOperator(e.target.value as "lt" | "lte")}>
            <option value="lt">strictly below</option>
            <option value="lte">at most</option>
          </select>
          <label>Threshold (USD, delivered total incl. shipping, tax, fees)</label>
          <input value={threshold} onChange={(e) => setThreshold(e.target.value)} />
          {thresholdMinor !== null && (
            <p className="muted">
              Largest qualifying total: <strong>{money(operator === "lt" ? thresholdMinor - 1 : thresholdMinor)}</strong>
            </p>
          )}
          <label>Deadline (ISO 8601 with offset)</label>
          <input value={expiresAt} onChange={(e) => setExpiresAt(e.target.value)} placeholder="2026-09-27T23:59:59+05:30" />
          <label>Time zone</label>
          <input value={timezone} onChange={(e) => setTimezone(e.target.value)} />
          <label>Delivery destination (required for tax and shipping)</label>
          <select value={destination} onChange={(e) => setDestination(e.target.value)}>
            <option value="">— missing —</option>
            {destinations.map((d) => (
              <option key={d.id} value={d.id}>
                {d.label} ({d.id})
              </option>
            ))}
          </select>
          <label>Authorization profile</label>
          <select value={profile} onChange={(e) => setProfile(e.target.value as "ap2" | "vi")}>
            <option value="ap2">AP2 {catalog?.profiles.ap2 ? `(${catalog.profiles.ap2})` : ""}</option>
            <option value="vi">Verifiable Intent {catalog?.profiles.vi ? `(${catalog.profiles.vi})` : ""}</option>
          </select>
        </div>
      </div>

      <div className="panel">
        <div className="row">
          <button className="secondary" onClick={doPreview}>
            Preview normalized rule
          </button>
          <button onClick={create} disabled={busy || !product || merchants.length === 0 || !expiresAt || thresholdMinor === null}>
            Create draft → review authorization
          </button>
        </div>
        {error && <pre className="error">{error}</pre>}
        {preview && (
          <>
            <h2>What will be signed</h2>
            <ul className="sentences">
              {preview.sentences.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
            {preview.unknowns.length > 0 && (
              <ul className="sentences">
                {preview.unknowns.map((u) => (
                  <li key={u} className="error">
                    {u}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </>
  );
}
