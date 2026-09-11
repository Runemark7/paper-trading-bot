import { useState, type MouseEvent, type ReactNode } from "react";

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
    <div className={`min-w-0 rounded-xl border border-white/10 bg-white/[0.03] p-3 sm:p-4 ${className}`}>
      {(title || aside) && (
        <div className="flex flex-col gap-1 mb-3 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
          {title && (
            <div className="text-xs uppercase tracking-wider text-white/40 min-w-0 break-words">{title}</div>
          )}
          {aside && <div className="text-xs text-white/40 min-w-0 break-words sm:text-right">{aside}</div>}
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
    <section className={`min-w-0 rounded-xl border border-white/10 bg-white/[0.03] p-3 sm:p-4 border-l-4 ${bar}`}>
      <div className="flex flex-col gap-2 mb-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
        <div className="min-w-0">
          <div className={`text-xs uppercase tracking-wider font-semibold ${kick}`}>{kicker}</div>
          <h2 className="text-lg font-semibold text-white mt-0.5">{title}</h2>
        </div>
        {aside && <div className="text-xs text-white/45 min-w-0 break-words sm:text-right sm:max-w-sm">{aside}</div>}
      </div>
      {children}
    </section>
  );
}

export function Stat({ label, value, tone, hint }: { label: string; value: string; tone?: "pos" | "neg" | "neutral"; hint?: string }) {
  const color =
    tone === "pos" ? "text-emerald-400" : tone === "neg" ? "text-rose-400" : "text-white";
  return (
    <div className="min-w-0 rounded-xl border border-white/10 bg-white/[0.03] p-3 sm:p-4">
      <div className="text-xs uppercase tracking-wider text-white/40">{label}</div>
      <div className={`text-xl sm:text-2xl font-semibold mt-1 break-words ${color}`}>{value}</div>
      {hint && <div className="text-xs text-white/40 mt-1 break-words">{hint}</div>}
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
    <span className={`inline-block max-w-full px-2.5 py-1 rounded-full text-xs font-medium break-words whitespace-normal ${map[tone]}`}>
      {children}
    </span>
  );
}

/** Long strategy / account names: wrap the full string; native title for hover/long-press. */
export function MonoName({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  const title = typeof children === "string" ? children : undefined;
  return (
    <span
      title={title}
      className={`font-mono min-w-0 max-w-full break-all [overflow-wrap:anywhere] whitespace-normal select-text ${className}`}
    >
      {children}
    </span>
  );
}

/** Queued / in-flight name pills — wrap + title so the full string is reachable. */
export function NameChip({
  name,
  className = "",
}: {
  name: string;
  className?: string;
}) {
  return (
    <span
      title={name}
      className={`inline-flex max-w-full min-w-0 items-center rounded-md px-1.5 py-0.5 font-mono text-[11px] break-all [overflow-wrap:anywhere] whitespace-normal select-text ${className}`}
    >
      {name}
    </span>
  );
}

/** Copyable full strategy string for expanded / detail surfaces (names inside tap-targets are not selectable). */
export function CopyableName({
  name,
  className = "",
}: {
  name: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  async function copy(e: MouseEvent<HTMLButtonElement>) {
    e.preventDefault();
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(name);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* text remains selectable via MonoName */
    }
  }

  return (
    <div className={`flex flex-col gap-1 min-w-0 sm:flex-row sm:items-start sm:gap-2 ${className}`}>
      <MonoName className="w-full sm:flex-1 text-sm text-white select-all">{name}</MonoName>
      <button
        type="button"
        onClick={copy}
        className="self-start shrink-0 min-h-11 px-2.5 text-[11px] text-white/55 hover:text-white rounded bg-white/5 hover:bg-white/10"
        aria-label={`Copy strategy name ${name}`}
        title="Copy full name"
      >
        {copied ? "copied" : "copy"}
      </button>
    </div>
  );
}

export function Field({
  label,
  children,
  className = "",
  span = false,
  mono = false,
}: {
  label: string;
  children: ReactNode;
  className?: string;
  /** Full-width row in a 2-col FieldGrid — use for long condition ids. */
  span?: boolean;
  mono?: boolean;
}) {
  const title = typeof children === "string" ? children : undefined;
  return (
    <div className={`min-w-0 overflow-hidden ${span ? "col-span-2" : ""} ${className}`}>
      <div className="text-[11px] uppercase tracking-wider text-white/40">{label}</div>
      <div
        title={title}
        className={`mt-0.5 text-sm min-w-0 overflow-hidden break-all [overflow-wrap:anywhere] ${
          mono ? "font-mono" : ""
        }`}
      >
        {children}
      </div>
    </div>
  );
}

/** Two-column phone metric grid. Children need min-w-0 (Field provides it). */
export function FieldGrid({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-2 gap-2 min-w-0 overflow-hidden">{children}</div>;
}

/** Phone: stacked cards. md+: keep the existing table (caller supplies both). */
export function PhoneCards({ children }: { children: ReactNode }) {
  return <ul className="md:hidden space-y-3 min-w-0">{children}</ul>;
}

export function DesktopTable({ children }: { children: ReactNode }) {
  return <div className="hidden md:block overflow-x-auto min-w-0">{children}</div>;
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
