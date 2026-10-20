const SUCCESS = new Set(["confirmed", "completed", "verified", "converted_to_appointment", "resolved", "active", "account_configured"]);
const WARNING = new Set(["waiting", "submitted", "awaiting", "awaiting_payment", "payment_submitted", "pending", "open", "invited"]);
const DANGER = new Set(["cancelled", "rejected", "payment_rejected", "no_show", "inactive", "expired", "invitation_problem"]);
const INFO = new Set(["scheduled", "checked_in", "in_progress", "payment_verified", "awaiting_slot_selection", "in_progress"]);

export function StatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const tone = SUCCESS.has(normalized) ? "success" : WARNING.has(normalized) ? "warning" : DANGER.has(normalized) ? "danger" : INFO.has(normalized) ? "info" : "neutral";
  return <span className={`status-badge status-${tone}`}>{normalized.replaceAll("_", " ")}</span>;
}
