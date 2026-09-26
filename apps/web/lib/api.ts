"use client";

export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export function getToken(): string {
  if (typeof window === "undefined") return "demo-token";
  return window.localStorage.getItem("pw_token") || "demo-token";
}

export function setToken(token: string) {
  window.localStorage.setItem("pw_token", token);
}

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown) {
    super(typeof body === "object" && body && "message" in (body as Record<string, unknown>) ? String((body as Record<string, unknown>).message) : `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

function idempotencyKey(): string {
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export async function api<T>(path: string, init: RequestInit & { mutation?: boolean; ifMatch?: number } = {}): Promise<T> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${getToken()}`,
    ...(init.headers as Record<string, string> | undefined),
  };
  if (init.body) headers["Content-Type"] = "application/json";
  if (init.mutation) headers["Idempotency-Key"] = idempotencyKey();
  if (init.ifMatch !== undefined) headers["If-Match"] = String(init.ifMatch);
  const res = await fetch(`${API_URL}${path}`, { ...init, headers, cache: "no-store" });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) throw new ApiError(res.status, body?.detail ?? body);
  return body as T;
}

// ---------------------------------------------------------------- types
export interface Watch {
  id: string;
  version: number;
  status: string;
  original_request: string | null;
  product_constraints: { product_id: string; title: string; attributes: Record<string, string>; bundle: boolean };
  merchant_sku_mapping: Record<string, string>;
  price_operator: "lt" | "lte";
  threshold_minor: number;
  threshold_display: string;
  inclusive_ceiling_minor: number;
  currency: string;
  quantity: number;
  destination_id: string | null;
  allowed_merchants: string[];
  max_purchases: number;
  purchases_completed: number;
  authorization_profile: "ap2" | "vi";
  expires_at: string;
  timezone: string;
  next_check_at: string | null;
  cancel_requested: boolean;
  created_at: string;
  updated_at: string;
  authorization?: Authorization | null;
  active_attempt?: Attempt | null;
  clock?: { now: string; simulated: boolean };
}

export interface Authorization {
  id: string;
  profile: string;
  profile_version: string;
  status: string;
  presentation_state: string;
  expires_at: string;
  agent_key_id: string;
  summary: Record<string, unknown>;
}

export interface Observation {
  id: string;
  merchant_id: string;
  sku: string | null;
  total_minor: number | null;
  total_display?: string;
  currency: string | null;
  observed_at: string;
  valid_until: string | null;
  eligibility: string;
  reasons: string[];
  source_reference: string;
  price_components: { item_subtotal: number | null; shipping: number | null; tax: number | null; fees: number | null; discount: number | null; delivered_total: number | null; source: string };
  quantity_available: number | null;
}

export interface Event {
  id: number;
  at: string;
  type: string;
  actor: string;
  watch_version: number | null;
  details: Record<string, unknown>;
}

export interface Attempt {
  id: string;
  merchant_id: string;
  checkout_id: string;
  final_total_minor: number;
  currency: string;
  idempotency_key: string;
  claim_state: string;
  order_state: string;
  payment_state: string;
  external_references: Record<string, string>;
  claimed_at: string;
  submitted_at: string | null;
  resolved_at: string | null;
  last_error: string | null;
}

export interface PurchaseDetail {
  attempts: Attempt[];
  outbound_events: Record<string, { id: string; kind: string; state: string; idempotency_key: string; payload_digest: string; sent_at: string | null; acknowledged_at: string | null }[]>;
  reconciliation_cases: { id: string; attempt_id: string; state: string; summary: string; checks: number; resolution: string | null; opened_at: string; resolved_at: string | null }[];
  authorizations: Authorization[];
  evidence: { artifact_id: string; kind: string; digest: string; attempt_id: string | null; created_at: string }[];
  notifications: { id: string; subject: string; body: string; state: string; created_at: string }[];
}

export interface Proposal {
  watch_id: string;
  watch_version: number;
  profile: string;
  profile_version: string;
  rule: { executable_rule: Record<string, unknown>; inclusive_ceiling_minor: number; sentences: string[]; unknowns: string[] };
  delegation: { merchants: { id: string; name: string; website: string }[]; acceptable_items: { id: string; title: string; merchant_id: string }[]; quantity: number; currency: string; inclusive_max_minor: number; not_after: string; payment_instrument: Record<string, string>; agent_key_id: string };
  can_activate: boolean;
}

export interface Catalog {
  products: { product_id: string; title: string; attributes: Record<string, string>; bundle: boolean }[];
  merchants: { id: string; name: string; website: string; items: { sku: string; title: string; canonical_product_id: string; attributes: Record<string, string>; bundle: boolean }[] }[];
  profiles: Record<string, string>;
}

export function money(minor: number | null | undefined, currency = "USD"): string {
  if (minor === null || minor === undefined) return "—";
  const sign = minor < 0 ? "-" : "";
  const abs = Math.abs(minor);
  return `${sign}${currency} ${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}
