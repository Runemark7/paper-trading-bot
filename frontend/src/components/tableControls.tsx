import type { SortDir } from "../status/championFilters";

export function FilterInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
}) {
  const id = `col-filter-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <label className="min-w-0 block" htmlFor={id}>
      <span className="text-[11px] uppercase tracking-wider text-white/40">{label}</span>
      <input
        id={id}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={`Filter ${label}`}
        className="mt-1 w-full min-h-11 rounded-md bg-[#121a38] px-2 text-xs text-white ring-1 ring-white/15 placeholder:text-white/30"
      />
    </label>
  );
}

export function SortTh<K extends string>({
  label,
  column,
  sort,
  onCycle,
  align = "left",
}: {
  label: string;
  column: K;
  sort: { key: K; dir: SortDir };
  onCycle: (key: K) => void;
  align?: "left" | "right";
}) {
  const active = sort.key === column;
  const arrow = active ? (sort.dir === "asc" ? "↑" : "↓") : "↕";
  const ariaSort = active ? (sort.dir === "asc" ? "ascending" : "descending") : "none";
  return (
    <th className={`${align === "right" ? "text-right" : "text-left"} py-1 px-1.5`} aria-sort={ariaSort}>
      <button
        type="button"
        onClick={() => onCycle(column)}
        aria-label={`Sort by ${label}`}
        className={`inline-flex items-center gap-1 min-h-11 uppercase tracking-wider whitespace-nowrap ${
          align === "right" ? "ml-auto" : ""
        } ${active ? "text-white" : "text-white/40 hover:text-white/70"}`}
      >
        {label}
        <span className={active ? "text-white/80" : "text-white/25"} aria-hidden="true">
          {arrow}
        </span>
      </button>
    </th>
  );
}
