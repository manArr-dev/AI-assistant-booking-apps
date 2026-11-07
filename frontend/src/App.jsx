import { useEffect, useState } from "react";
import { useAuth } from "./auth.jsx";
import SignIn from "./pages/SignIn.jsx";
import SearchPage from "./pages/SearchPage.jsx";
import Trips from "./pages/Trips.jsx";
import Admin from "./pages/Admin.jsx";

// The booking bar. `role` limits a tab to that role; the server enforces the same rule.
const TABS = [
  { id: "flight", label: "Flights", icon: "✈" },
  { id: "hotel", label: "Hotels", icon: "🏨" },
  { id: "car", label: "Cars", icon: "🚗" },
  { id: "train", label: "Trains", icon: "🚆" },
  { id: "trips", label: "My trips", icon: "🧳" },
  { id: "admin", label: "Admin", icon: "🛡", role: "admin" },
];
const BOOKABLE = ["flight", "hotel", "car", "train"];

function useHashTab(fallback) {
  const read = () => window.location.hash.replace("#", "") || fallback;
  const [tab, setTab] = useState(read);
  useEffect(() => {
    const onHash = () => setTab(read());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  });
  return [tab, (id) => (window.location.hash = id)];
}

export default function App() {
  const { user, loading, signOut } = useAuth();
  const [tab, setTab] = useHashTab("flight");

  if (loading) return <div className="center muted">Loading…</div>;
  if (!user) return <SignIn />;

  const tabs = TABS.filter((t) => !t.role || t.role === user.role);
  const active = tabs.some((t) => t.id === tab) ? tab : "flight";

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">Travel Booking</div>
        <div className="who">
          <span className="who-name">{user.full_name}</span>
          <span className={`badge role-${user.role}`}>{user.role}</span>
          <button className="btn ghost small" onClick={signOut}>Sign out</button>
        </div>
      </header>

      <nav className="tabbar" aria-label="Booking type">
        {tabs.map((t) => (
          <button
            key={t.id}
            className={`tab ${active === t.id ? "active" : ""}`}
            aria-current={active === t.id ? "page" : undefined}
            onClick={() => setTab(t.id)}
          >
            <span className="tab-icon" aria-hidden="true">{t.icon}</span>
            <span>{t.label}</span>
          </button>
        ))}
      </nav>

      <main className="content">
        {BOOKABLE.includes(active) && <SearchPage key={active} kind={active} />}
        {active === "trips" && <Trips />}
        {active === "admin" && <Admin />}
      </main>
    </div>
  );
}
