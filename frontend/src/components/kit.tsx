import { useId, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { CATEGORY_LABEL, CATEGORY_TONE, strategyBlocks } from "../lib/rules";
import { gateInfo } from "../lib/gates";

/** Hover / focus / tap explainer. Keyboard reachable; no portal, no JS positioning. */
export function InfoTip({ label, children, align = "center" }: { label: string; children: ReactNode; align?: "center" | "left" | "right" }) {
  const id = useId();
  const pos =
    align === "left" ? "left-0" : align === "right" ? "right-0" : "left-1/2 -translate-x-1/2";
  return (
    <span className="group relative inline-flex align-middle">
      <button
        type="button"
        aria-label={`What is ${label}?`}
        aria-describedby={id}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-white/10 text-[10px] font-semibold text-white/60 hover:bg-white/20 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400"
      >
        ?
      </button>
      <span
        role="tooltip"
        id={id}
        className={`pointer-events-none invisible absolute top-6 z-50 w-64 max-w-[78vw] rounded-lg border border-white/10 bg-[#141c3d] p-3 text-left text-xs font-normal normal-case tracking-normal leading-relaxed text-white/80 opacity-0 shadow-xl shadow-black/40 transition group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100 ${pos}`}
      >
        <span className="block font-semibold text-white mb-1">{label}</span>
        {children}
      </span>
    </span>
  );
}

export function PageHeader({
  kicker,
  title,
  children,
  accent = "indigo",
  aside,
}: {
  kicker: string;
  title: string;
  children?: ReactNode;
  accent?: "indigo" | "amber" | "emerald";
  aside?: ReactNode;
}) {
  const kick = accent === "amber" ? "text-amber-300" : accent === "emerald" ? "text-emerald-300" : "text-indigo-300";
  return (
    <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between min-w-0">
      <div className="min-w-0 max-w-3xl">
        <div className={`text-xs font-semibold uppercase tracking-[0.14em] ${kick}`}>{kicker}</div>
        <h1 className="mt-1 text-2xl sm:text-3xl font-semibold tracking-tight text-white">{title}</h1>
        {children ? <div className="mt-2 text-sm sm:text-[15px] leading-relaxed text-white/65">{children}</div> : null}
      </div>
      {aside ? <div className="shrink-0 text-xs text-white/45 md:text-right">{aside}</div> : null}
    </header>
  );
}

export function SectionTitle({ title, children, aside, info }: { title: string; children?: ReactNode; aside?: ReactNode; info?: ReactNode }) {
  return (
    <div className="mb-3 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between min-w-0">
      <div className="min-w-0">
        <h2 className="flex items-center gap-2 text-base font-semibold text-white">
          {title}
          {info}
        </h2>
        {children ? <p className="mt-0.5 text-sm text-white/55 leading-relaxed">{children}</p> : null}
      </div>
      {aside ? <div className="text-xs text-white/40 sm:text-right shrink-0">{aside}</div> : null}
    </div>
  );
}

export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-2xl border border-white/[0.08] bg-gradient-to-b from-white/[0.045] to-white/[0.02] p-4 sm:p-5 ${className}`}>
      {children}
    </section>
  );
}

export function Kpi({
  label,
  value,
  hint,
  tone = "neutral",
  info,
  loading = false,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "pos" | "neg" | "neutral" | "accent";
  info?: ReactNode;
  loading?: boolean;
}) {
  const color =
    tone === "pos" ? "text-emerald-300" : tone === "neg" ? "text-rose-300" : tone === "accent" ? "text-indigo-200" : "text-white";
  return (
    <div className="min-w-0 rounded-xl border border-white/[0.08] bg-white/[0.03] p-3 sm:p-4">
      <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-white/45">
        <span className="min-w-0 truncate">{label}</span>
        {info}
      </div>
      {loading ? (
        <Skeleton className="mt-2 h-7 w-20" />
      ) : (
        <div className={`mt-1 text-xl sm:text-2xl font-semibold tabular-nums break-words ${color}`}>{value}</div>
      )}
      {hint ? <div className="mt-1 text-xs text-white/45 break-words">{hint}</div> : null}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <span aria-hidden className={`block animate-pulse rounded-md bg-white/10 ${className}`} />;
}

export function SkeletonRows({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

/** Load error. With last-known data still on screen, say so instead of hiding it. */
export function ErrorNote({ what, error, stale = false }: { what: string; error: unknown; stale?: boolean }) {
  return (
    <div role="alert" className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-100 break-words">
      Could not load {what}
      {stale ? " — showing the last data we had." : "."}{" "}
      <span className="text-rose-200/70 text-xs">{error instanceof Error ? error.message : String(error ?? "")}</span>
    </div>
  );
}

/** Explains an empty state instead of leaving a blank box. */
export function EmptyState({ title, children, icon = "○" }: { title: string; children?: ReactNode; icon?: string }) {
  return (
    <div className="rounded-xl border border-dashed border-white/15 bg-white/[0.015] px-4 py-6 text-center">
      <div aria-hidden className="text-2xl text-white/25">{icon}</div>
      <div className="mt-1 text-sm font-medium text-white/80">{title}</div>
      {children ? <div className="mx-auto mt-1 max-w-md text-sm leading-relaxed text-white/50">{children}</div> : null}
    </div>
  );
}

/** Strategy rules as readable chips. The raw block stays in the tooltip and in mono beneath. */
export function RuleList({ name, dense = false }: { name: string; dense?: boolean }) {
  const blocks = strategyBlocks(name);
  if (!blocks.length) return null;
  return (
    <ul className={`flex flex-col ${dense ? "gap-1" : "gap-1.5"} min-w-0`}>
      {blocks.map((b, i) => (
        <li key={`${b.raw}-${i}`} className="flex items-start gap-2 min-w-0">
          <span
            className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ${CATEGORY_TONE[b.category]}`}
          >
            {CATEGORY_LABEL[b.category]}
          </span>
          <span className="min-w-0 text-sm text-white/85 leading-snug">
            {b.text}
            {!dense ? (
              <span className="ml-1.5 font-mono text-[11px] text-white/35 break-all [overflow-wrap:anywhere]">{b.raw}</span>
            ) : null}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Compact category chips for tables: one chip per block, full text on hover. */
export function BlockChips({ name }: { name: string }) {
  const blocks = strategyBlocks(name);
  return (
    <span className="flex flex-wrap gap-1 min-w-0">
      {blocks.map((b, i) => (
        <span
          key={`${b.raw}-${i}`}
          title={`${b.text} (${b.raw})`}
          className={`inline-flex max-w-full items-center rounded px-1.5 py-0.5 font-mono text-[10px] ring-1 break-all [overflow-wrap:anywhere] ${CATEGORY_TONE[b.category]}`}
        >
          {b.raw}
        </span>
      ))}
    </span>
  );
}

export function GatePill({ gate, failed = true }: { gate: string; failed?: boolean }) {
  const info = gateInfo(gate);
  const tone = failed ? "bg-rose-500/15 text-rose-200 ring-rose-400/25" : "bg-emerald-500/15 text-emerald-200 ring-emerald-400/25";
  return (
    <span
      title={`${info.label}: ${info.rule}. ${info.plain}`}
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${tone}`}
    >
      <span aria-hidden>{failed ? "✕" : "✓"}</span>
      {info.label}
    </span>
  );
}

export function Funnel({ steps }: { steps: { label: string; value: number | null | undefined; hint?: string; tone?: "accent" | "pos" | "gold" }[] }) {
  const max = Math.max(1, ...steps.map((s) => s.value ?? 0));
  return (
    <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4 min-w-0">
      {steps.map((s, i) => {
        const prev = i > 0 ? steps[i - 1].value : null;
        const conv = prev && s.value != null ? s.value / prev : null;
        const width = s.value != null ? Math.max(4, Math.round((Math.log10(s.value + 1) / Math.log10(max + 1)) * 100)) : 0;
        const bar = s.tone === "gold" ? "bg-amber-400" : s.tone === "pos" ? "bg-emerald-400" : "bg-indigo-400";
        return (
          <li key={s.label} className="relative min-w-0 rounded-xl border border-white/[0.08] bg-white/[0.03] p-3">
            <div className="flex items-center justify-between gap-2 text-[11px] uppercase tracking-wider text-white/45">
              <span className="min-w-0 truncate">
                <span className="text-white/30 mr-1">{i + 1}</span>
                {s.label}
              </span>
              {conv != null ? (
                <span className="shrink-0 rounded bg-white/5 px-1 text-[10px] normal-case tracking-normal text-white/50" title="share of the previous step">
                  {conv >= 0.01 || conv === 0 ? `${(conv * 100).toFixed(conv < 0.1 ? 1 : 0)}%` : `${(conv * 100).toFixed(2)}%`}
                </span>
              ) : null}
            </div>
            <div className="mt-1 text-2xl font-semibold tabular-nums text-white">
              {s.value != null ? s.value.toLocaleString("en-US") : <Skeleton className="h-7 w-16" />}
            </div>
            <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-white/5">
              <div className={`h-full rounded-full ${bar}`} style={{ width: `${width}%` }} />
            </div>
            {s.hint ? <div className="mt-1.5 text-xs text-white/45 leading-snug">{s.hint}</div> : null}
          </li>
        );
      })}
    </ol>
  );
}

/** Collapsible section whose body mounts only once opened (keeps heavy queries off the default view). */
export function Disclosure({
  title,
  summary,
  children,
  defaultOpen = false,
}: {
  title: string;
  summary?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="min-w-0 rounded-2xl border border-white/[0.08] bg-white/[0.02]">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full min-h-12 items-center justify-between gap-3 px-4 py-3 text-left hover:bg-white/[0.03] rounded-2xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400"
      >
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-white">{title}</span>
          {summary ? <span className="block text-xs text-white/50 mt-0.5">{summary}</span> : null}
        </span>
        <span aria-hidden className={`shrink-0 text-white/50 transition ${open ? "rotate-90" : ""}`}>
          ▸
        </span>
      </button>
      {open ? <div className="border-t border-white/[0.06] p-3 sm:p-4 space-y-4">{children}</div> : null}
    </section>
  );
}

export function AreaLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      className="inline-flex min-h-11 items-center gap-1.5 rounded-lg bg-white/10 px-3.5 py-2 text-sm font-medium text-white hover:bg-white/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400"
    >
      {children}
      <span aria-hidden>→</span>
    </Link>
  );
}

export function StatusDot({ tone }: { tone: "pos" | "warn" | "neg" | "idle" }) {
  const c =
    tone === "pos" ? "bg-emerald-400" : tone === "warn" ? "bg-amber-400" : tone === "neg" ? "bg-rose-400" : "bg-white/30";
  return (
    <span className="relative inline-flex h-2 w-2 shrink-0">
      {tone === "pos" ? <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-50 ${c}`} /> : null}
      <span className={`relative inline-flex h-2 w-2 rounded-full ${c}`} />
    </span>
  );
}

export function usd(n: number | null | undefined, digits = 2, signed = false): string {
  if (n == null || Number.isNaN(n)) return "—";
  const s = Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  if (signed) return `${n > 0 ? "+" : n < 0 ? "−" : ""}$${s}`;
  return `${n < 0 ? "−" : ""}$${s}`;
}

export function num(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toFixed(digits);
}

export function toneOf(n: number | null | undefined): "pos" | "neg" | "neutral" {
  if (n == null || Number.isNaN(n) || n === 0) return "neutral";
  return n > 0 ? "pos" : "neg";
}

export function shortAgo(iso?: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  const abs = Math.abs(sec);
  const rel = abs < 60 ? `${abs}s` : abs < 3600 ? `${Math.round(abs / 60)}m` : abs < 86400 ? `${Math.round(abs / 3600)}h` : `${Math.round(abs / 86400)}d`;
  return sec >= 0 ? `${rel} ago` : `in ${rel}`;
}

export function utcStamp(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toISOString().replace("T", " ").slice(0, 16)} UTC`;
}
