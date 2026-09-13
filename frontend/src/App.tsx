import { NavLink, Routes, Route, Navigate } from "react-router-dom";
import Overview from "./pages/Overview";
import Positions from "./pages/Positions";
import Champions from "./pages/Champions";
import ChampionDetail from "./pages/ChampionDetail";
import Discovery from "./pages/Discovery";
import Learning from "./pages/Learning";
import ChartPage from "./pages/Chart";
import StatusBar from "./status/StatusBar";

const nav = [
  { to: "/", label: "Overview" },
  { to: "/chart", label: "Chart" },
  { to: "/positions", label: "Positions" },
  { to: "/champions", label: "Champions" },
  { to: "/discovery", label: "Discovery" },
  { to: "/learning", label: "Learning" },
];

function navClass(isActive: boolean, compact: boolean) {
  const base = compact
    ? "flex min-h-12 flex-col items-center justify-center px-0.5 text-[10px] leading-tight font-medium"
    : "px-3 py-1.5 rounded-md text-sm font-medium transition";
  return `${base} ${
    isActive
      ? compact
        ? "text-white bg-white/10"
        : "bg-indigo-600 text-white"
      : compact
        ? "text-white/55"
        : "text-white/70 hover:text-white hover:bg-white/5"
  }`;
}

export default function App() {
  return (
    <div className="min-h-screen min-w-0">
      <header className="border-b border-white/10 bg-[#0d1430]/80 px-4 py-3 md:px-6">
        <div className="flex items-center justify-between gap-4 min-w-0">
          <h1 className="text-base md:text-lg font-semibold tracking-tight min-w-0 truncate">
            PaperBot{" "}
            <span className="text-white/40 font-normal hidden sm:inline">
              — paper tournament · BTC/ETH
            </span>
          </h1>
          <nav className="hidden md:flex gap-1 shrink-0">
            {nav.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.to === "/"}
                className={({ isActive }) => navClass(isActive, false)}
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <StatusBar />
      <main className="max-w-7xl mx-auto min-w-0 px-4 py-4 md:px-6 md:py-6 pb-24 md:pb-6">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/chart" element={<ChartPage />} />
          <Route path="/positions" element={<Positions />} />
          <Route path="/champions" element={<Champions />} />
          <Route path="/champions/*" element={<ChampionDetail />} />
          <Route path="/discovery" element={<Discovery />} />
          <Route path="/learning" element={<Learning />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <nav
        className="md:hidden fixed bottom-0 inset-x-0 z-40 border-t border-white/10 bg-[#0d1430]/95 pb-[env(safe-area-inset-bottom)]"
        aria-label="Primary"
      >
        <div className="grid grid-cols-6">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              className={({ isActive }) => navClass(isActive, true)}
            >
              {n.label}
            </NavLink>
          ))}
        </div>
      </nav>
    </div>
  );
}
