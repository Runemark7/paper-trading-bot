import type { Champion } from "../api/types";
import { Badge } from "../components/ui";

/** Seated without a qualified gate verdict. Paper forward test only. */
export const UNPROVEN_FORWARD_ADMISSION = "forward_test_unproven";

export function isUnprovenForwardTest(champ: Pick<Champion, "admission">): boolean {
  return champ.admission === UNPROVEN_FORWARD_ADMISSION;
}

export function AdmissionBadge({ admission }: { admission?: string | null }) {
  if (admission !== UNPROVEN_FORWARD_ADMISSION) return null;
  return <Badge tone="warn">Unproven - forward test</Badge>;
}
