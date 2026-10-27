import type { ForwardStatus } from "../api/types";
import { Badge } from "../components/ui";

/** Badge for the pre-registered forward test. Absence of a status renders nothing. */
export function ForwardStatusBadge({ status }: { status?: ForwardStatus | null }) {
  const s = status?.status;
  if (s === "flagged") return <Badge tone="warn">forward flagged</Badge>;
  if (s === "ok") return <Badge tone="pos">forward ok</Badge>;
  if (s === "pending") return <Badge tone="wait">forward pending</Badge>;
  if (s === "not_registered") return <Badge tone="neutral">forward not registered</Badge>;
  if (s === "unavailable") return <Badge tone="neutral">forward unavailable</Badge>;
  return null;
}

export function forwardStatusText(status?: ForwardStatus | null): string {
  if (!status) return "—";
  const reasons = (status.reasons ?? []).filter(Boolean).join("; ");
  if (status.status === "flagged") return reasons || "flagged for Alexander";
  if (status.status === "pending") return "waiting for 90 days and 20 closed trades";
  if (status.status === "ok") return "inside the pre-registered bounds";
  if (status.status === "not_registered") return "no criteria stored at admission";
  if (status.status === "unavailable") return reasons || "unavailable";
  return status.status;
}
