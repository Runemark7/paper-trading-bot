import { useEffect, useState } from "react";
import { NavLink, Routes, Route, Navigate, useLocation } from "react-router-dom";
import Overview from "./pages/Overview";
import Positions from "./pages/Positions";
import Champions from "./pages/Champions";
import ChampionDetail from "./pages/ChampionDetail";
import Discovery from "./pages/Discovery";
import Learning from "./pages/Learning";
import ChartPage from "./pages/Chart";
import StatusBar from "./status/StatusBar";
import PageErrorBoundary from "./components/PageErrorBoundary";

const nav = [
  { to: "/", label: "Overview", icon: "◎" },
  { to: "/discovery", label: "Discovery", icon: "⌕" },
  { to: "/champions", label: "Champions", icon: "♛" },
];

const tools = [
  { to: "/chart", label: "Chart" },
  { to: "/positions", label: "Positions" },
  { to: "/learning", label: "Learning" },
];

function Logo() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className="h-8 w-8 shrink-0">
      <defs>
        <linearGradient id="pb-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#818cf8" />
          <stop offset="1" stopColor="#f59e0b" />
        </linearGradient>
      </defs>
      <rect x="1" y="1" width="30" height="30" rx="9" fill="#121a3a" stroke="url(#pb-g)" strokeWidth="1.5" />
      <path d="M7 21l5-6 4 3 6-8 3 3" fill="none" stroke="url(#pb-g)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function desktopClass(isActive: boolean) {
  return `px-3 py-2 rounded-lg text-sm font-medium transition ${
    isActive ? "bg-white/10 text-white" : "text-white/65 hover:text-white hover:bg-white/5"
  }`;
}

function toolClass(isActive: boolean) {
  return `px-2.5 py-1.5 rounded-md text-xs font-medium transition ${
    isActive ? "text-white bg-white/10" : "text-white/45 hover:text-white/80"
  }`;
}

function tabClass(isActive: boolean) {
  return `flex min-h-14 flex-col items-center justify-center gap-0.5 text-[11px] font-medium ${
    isActive ? "text-white" : "text-white/50"
  }`;
}

export default function App() {
  const location = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);
  useEffect(() => setMoreOpen(false), [location.pathname]);
  const onTool = tools.some((t) => location.pathname.startsWith(t.to));

  return (
    <div className="min-h-screen min-w-0">
      <header className="sticky top-0 z-40 border-b border-white/[0.07] bg-[#0b1020]/85 backdrop-blur supports-[backdrop-filter]:bg-[#0b1020]/70 px-4 md:px-6">
        <div className="max-w-7xl mx-auto flex h-14 items-center justify-between gap-4 min-w-0">
          <NavLink to="/" className="flex items-center gap-2.5 min-w-0">
            <Logo />
            <span className="min-w-0">
              <span className="block text-[15px] font-semibold leading-tight tracking-tight text-white">PaperBot</span>
              <span className="block truncate text-[11px] leading-tight text-white/45">BTC strategy research lab</span>
            </span>
          </NavLink>
          <nav className="hidden md:flex items-center gap-1" aria-label="Primary">
            {nav.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.to === "/"} className={({ isActive }) => desktopClass(isActive)}>
                {n.label}
              </NavLink>
            ))}
            <span className="mx-2 h-5 w-px bg-white/10" aria-hidden />
            {tools.map((n) => (
              <NavLink key={n.to} to={n.to} className={({ isActive }) => toolClass(isActive)}>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <span
            className="shrink-0 rounded-full bg-sky-500/15 px-2.5 py-1 text-[11px] font-semibold text-sky-200 ring-1 ring-sky-400/30"
            title="Every trade on this site is simulated. No real money is ever traded."
          >
            Paper money only
          </span>
        </div>
      </header>
      <StatusBar />
      <main className="max-w-7xl mx-auto min-w-0 px-4 py-5 md:px-6 md:py-8 pb-28 md:pb-10">
        <PageErrorBoundary label="This page">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/chart" element={<ChartPage />} />
            <Route path="/positions" element={<Positions />} />
            <Route
              path="/champions"
              element={
                <PageErrorBoundary label="Champions">
                  <Champions />
                </PageErrorBoundary>
              }
            />
            <Route
              path="/champions/*"
              element={
                <PageErrorBoundary label="Champion detail">
                  <ChampionDetail />
                </PageErrorBoundary>
              }
            />
            <Route path="/discovery" element={<Discovery />} />
            <Route path="/learning" element={<Learning />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </PageErrorBoundary>
      </main>
      <footer className="hidden md:block border-t border-white/[0.06] px-6 py-6 text-center text-xs text-white/35">
        Paper trading research only — simulated fills on live Binance prices. Not investment advice, and no real funds are traded.
      </footer>
      {moreOpen ? (
        <div className="md:hidden fixed inset-x-0 bottom-[calc(3.5rem+env(safe-area-inset-bottom))] z-40 border-t border-white/10 bg-[#0d1430] p-2 shadow-2xl">
          <div className="grid grid-cols-3 gap-2">
            {tools.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                className={({ isActive }) =>
                  `flex min-h-12 items-center justify-center rounded-lg text-sm ${isActive ? "bg-white/10 text-white" : "bg-white/5 text-white/70"}`
                }
              >
                {n.label}
              </NavLink>
            ))}
          </div>
        </div>
      ) : null}
      <nav
        className="md:hidden fixed bottom-0 inset-x-0 z-50 border-t border-white/10 bg-[#0d1430]/95 backdrop-blur pb-[env(safe-area-inset-bottom)]"
        aria-label="Primary"
      >
        <div className="grid grid-cols-4">
          {nav.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"} className={({ isActive }) => tabClass(isActive)}>
              <span aria-hidden className="text-base leading-none">{n.icon}</span>
              {n.label}
            </NavLink>
          ))}
          <button
            type="button"
            aria-expanded={moreOpen}
            onClick={() => setMoreOpen((v) => !v)}
            className={tabClass(moreOpen || onTool)}
          >
            <span aria-hidden className="text-base leading-none">⋯</span>
            More
          </button>
        </div>
      </nav>
    </div>
  );
}
