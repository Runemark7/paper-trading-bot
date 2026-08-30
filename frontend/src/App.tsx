import { NavLink, Routes, Route, Navigate } from "react-router-dom";
import Overview from "./pages/Overview";
import Positions from "./pages/Positions";
import Champions from "./pages/Champions";
import Learning from "./pages/Learning";
import StatusBar from "./status/StatusBar";

const nav = [
  { to: "/", label: "Overview" },
  { to: "/positions", label: "Positions" },
  { to: "/champions", label: "Champions" },
  { to: "/learning", label: "Learning" },
];

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-white/10 bg-[#0d1430]/80 px-6 py-3">
        <div className="flex items-center gap-8">
          <h1 className="text-lg font-semibold tracking-tight">
            PaperBot <span className="text-white/40 font-normal">— paper tournament · BTC/ETH</span>
          </h1>
          <nav className="flex gap-1">
            {nav.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.to === "/"}
                className={({ isActive }) =>
                  `px-3 py-1.5 rounded-md text-sm font-medium transition ${
                    isActive
                      ? "bg-indigo-600 text-white"
                      : "text-white/70 hover:text-white hover:bg-white/5"
                  }`
                }
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <StatusBar />
      <main className="max-w-7xl mx-auto px-6 py-6">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/positions" element={<Positions />} />
          <Route path="/champions" element={<Champions />} />
          <Route path="/learning" element={<Learning />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}