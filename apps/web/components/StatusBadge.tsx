const TONE: Record<string, string> = {
  purchased: "ok",
  watching: "ok",
  eligible: "ok",
  captured: "ok",
  placed: "ok",
  active: "ok",
  paused: "warn",
  awaiting_authorization: "warn",
  draft: "warn",
  candidate_found: "warn",
  checkout_validating: "warn",
  purchase_claimed: "warn",
  submitting: "warn",
  reconciliation_required: "warn",
  incomplete: "warn",
  stale: "warn",
  pending: "warn",
  cancelled: "bad",
  expired: "bad",
  rejected: "bad",
  error: "bad",
  aborted: "bad",
  resolved_not_purchased: "bad",
  consumed: "",
};

export function StatusBadge({ value }: { value: string }) {
  return <span className={"badge " + (TONE[value] ?? "")}>{value.replace(/_/g, " ")}</span>;
}
