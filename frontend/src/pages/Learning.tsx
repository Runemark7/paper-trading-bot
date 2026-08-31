import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { fmt, Badge, Empty, Field, FieldGrid, PhoneCards, DesktopTable } from "../components/ui";

function levelTone(level?: string) {
  switch ((level ?? "").toLowerCase()) {
    case "established":
    case "trained":
      return "pos" as const;
    case "developing":
      return "warn" as const;
    default:
      return "neutral" as const;
  }
}

export default function Learning() {
  const learning = useQuery({ queryKey: ["learning"], queryFn: api.learning });

  const map = learning.data ?? {};
  const rows = Object.entries(map).map(([key, v]) => ({
    key,
    symbol: v.symbol,
    condition: v.condition,
    trials: v.trials,
    calibrated_prob: v.calibrated_prob,
    level: v.level,
  }));

  return (
    <div className="space-y-4 min-w-0">
      <div className="text-sm text-white/60">
        Last-known calibration from closed paper trades — not a running trainer.
        Each condition accumulates outcomes; calibrated p is measured proficiency.
      </div>

      {!rows.length ? (
        <Empty>No calibration rows yet. Skills appear after paper trades close.</Empty>
      ) : (
        <>
          <PhoneCards>
            {rows.map((r) => (
              <li key={r.key} className="rounded-lg border border-white/10 p-3 space-y-2 min-w-0 overflow-hidden">
                <div className="flex items-start justify-between gap-2 min-w-0">
                  <div className="font-medium min-w-0 truncate">{r.symbol}</div>
                  <span className="shrink-0">
                    <Badge tone={levelTone(r.level)}>{r.level ?? "learning"}</Badge>
                  </span>
                </div>
                <FieldGrid>
                  <Field label="Condition" span mono>
                    {r.condition}
                  </Field>
                  <Field label="Trials">{r.trials}</Field>
                  <Field label="Calibrated p">{fmt(r.calibrated_prob)}</Field>
                </FieldGrid>
              </li>
            ))}
          </PhoneCards>
          <DesktopTable>
            <table className="w-full text-sm">
              <thead className="text-white/40 text-xs uppercase">
                <tr>
                  <th className="text-left py-2">Symbol</th>
                  <th className="text-left">Condition</th>
                  <th className="text-right">Trials</th>
                  <th className="text-right">Calibrated p</th>
                  <th className="text-left">Level</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key} className="border-t border-white/5">
                    <td className="py-2 font-medium">{r.symbol}</td>
                    <td className="text-white/60 font-mono text-xs break-all">{r.condition}</td>
                    <td className="text-right">{r.trials}</td>
                    <td className="text-right">{fmt(r.calibrated_prob)}</td>
                    <td>
                      <Badge tone={levelTone(r.level)}>{r.level ?? "learning"}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </DesktopTable>
        </>
      )}
    </div>
  );
}
