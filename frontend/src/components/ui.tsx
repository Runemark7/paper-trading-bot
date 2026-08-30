import type { ReactNode } from "react";

export function Card({
  title,
  children,
  className = "",
  aside,
}: {
  title?: string;
  children: ReactNode;
  className?: string;
  aside?: ReactNode;
}) {
  return (
    <div className={`rounded-xl border border-white/10 bg-white/[0.03] p-4 ${className}`}>
      {(title || aside) && (
        <div className="flex items-start justify-between gap-3 mb-3">
          {title && (
            <div className="text-xs uppercase tracking-wider text-white/40">{title}</div>
          )}
          {aside && <div className="text-xs text-white/40">{aside}</div>}
        </div>
      )}
      {children}
    </div>
  );
}

export function Section({
  tone,
  kicker,
  title,
  aside,
  children,
}: {
  tone: "running" | "progress";
  kicker: string;
  title: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  const bar = tone === "running" ? "border-l-emerald-400" : "border-l-amber-400";
  const kick = tone === "running" ? "text-emerald-300" : "text-amber-300";
  return (
    <section className={`rounded-xl border border-white/10 bg-white/[0.03] p-4 border-l-4 ${bar}`}>
      <div className="flex items-start justify-between gap-4 mb-3">
        <div>
          <div className={`text-xs uppercase tracking-wider font-semibold ${kick}`}>{kicker}</div>
          <h2 className="text-lg font-semibold text-white mt-0.5">{title}</h2>
        </div>
        {aside && <div className="text-xs text-white/45 text-right max-w-sm">{aside}</div>}
      </div>
      {children}
    </section>
  );
}

export function Stat({ label, value, tone, hint }: { label: string; value: string; tone?: "pos" | "neg" | "neutral"; hint?: string }) {
  const color =
    tone === "pos" ? "text-emerald-400" : tone === "neg" ? "text-rose-400" : "text-white";
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <div className="text-xs uppercase tracking-wider text-white/40">{label}</div>
      <div className={`text-2xl font-semibold mt-1 ${color}`}>{value}</div>
      {hint && <div className="text-xs text-white/40 mt-1">{hint}</div>}
    </div>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "pos" | "neg" | "warn" | "neutral" | "run" | "wait" }) {
  const map = {
    pos: "bg-emerald-500/15 text-emerald-300",
    neg: "bg-rose-500/15 text-rose-300",
    warn: "bg-amber-500/15 text-amber-300",
    neutral: "bg-white/10 text-white/70",
    run: "bg-emerald-500/20 text-emerald-200 ring-1 ring-emerald-400/30",
    wait: "bg-amber-500/20 text-amber-200 ring-1 ring-amber-400/30",
  };
  return (
    <span className={`inline-block px-2.5 py-0.5 rounded-full text-xs font-medium ${map[tone]}`}>
      {children}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="text-white/50 text-sm leading-relaxed">{children}</div>;
}

export function fmt(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtPct(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${n >= 0 ? "+" : ""}${(n * 100).toFixed(2)}%`;
}
