import { BrowserRouter, Routes, Route, Navigate, NavLink } from "react-router-dom";
import Hub from "./pages/Hub";
import Drop from "./pages/Drop";
import DealBoard from "./pages/DealBoard";
import ReleaseWall from "./pages/ReleaseWall";
import WaveVault from "./pages/WaveVault";
import Onboarding from "./pages/Onboarding";
import Settings from "./pages/Settings";
import Sessions from "./pages/Sessions";
import Insights from "./pages/Insights";
import AuthGate from "./components/AuthGate";

const NAV_ITEMS = [
  { path: "/", label: "Hub", icon: "H", end: true },
  { path: "/drop", label: "Drop", icon: "D", end: false },
  { path: "/deals", label: "Deals", icon: "B", end: false },
  { path: "/releases", label: "Releases", icon: "R", end: false },
  { path: "/wave-vault", label: "Wave Vault", icon: "W", end: false },
  { path: "/insights", label: "Insights", icon: "◈", end: false },
  { path: "/sessions", label: "Sessions", icon: "⏱", end: false },
  { path: "/settings", label: "Settings", icon: "S", end: false },
] as const;

function Sidebar() {
  return (
    <nav className="w-16 sm:w-32 bg-surface-1 border-r border-surface-3 flex flex-col items-center sm:items-stretch py-6 px-0 sm:px-3 gap-2 shrink-0">
      {/* Logo mark */}
      <div className="w-9 h-9 bg-label-500 rounded-lg flex items-center justify-center text-black font-black text-xs mb-6 shrink-0 select-none">
        AL
      </div>

      {/* Nav links */}
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.path}
          to={item.path}
          end={item.end}
          title={item.label}
          aria-label={item.label}
          className={({ isActive }) =>
            `w-10 sm:w-full h-10 rounded-lg flex items-center justify-center sm:justify-start gap-2 sm:px-3 text-sm transition-colors
            ${
              isActive
                ? "bg-surface-2 text-zinc-100 shadow-inner"
                : "text-zinc-500 hover:text-zinc-100 hover:bg-surface-2"
            }`
          }
        >
          <span className="w-4 text-center font-mono text-xs">{item.icon}</span>
          <span className="hidden sm:inline truncate">{item.label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

export default function App() {
  return (
    <AuthGate>
      <BrowserRouter>
        <div className="flex h-[100dvh] overflow-hidden">
          <Sidebar />
          <main className="flex min-h-0 flex-1 overflow-hidden">
            <Routes>
              <Route path="/" element={<Hub />} />
              <Route path="/drop" element={<Drop />} />
              <Route path="/deals" element={<DealBoard />} />
              <Route path="/releases" element={<ReleaseWall />} />
              <Route path="/wave-vault" element={<WaveVault />} />
              <Route path="/insights" element={<Insights />} />
              <Route path="/sessions" element={<Sessions />} />
              <Route path="/onboarding" element={<Onboarding />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </AuthGate>
  );
}
