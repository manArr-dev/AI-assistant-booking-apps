import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { TripList, useCancel } from "./Trips.jsx";

// Only rendered for role=admin; the /api/admin/* endpoints also reject everyone else.
export default function Admin() {
  const [users, setUsers] = useState(null);
  const [trips, setTrips] = useState(null);
  const [filter, setFilter] = useState("all");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [u, t] = await Promise.all([api.get("/admin/users"), api.get("/admin/trips")]);
      setUsers(u.users);
      setTrips(t.trips);
    } catch (e) {
      setError(e.message);
    }
  }, []);
  const [cancel, cancelError] = useCancel(load);
  useEffect(() => { load(); }, [load]);

  const shown = (trips || []).filter((t) => filter === "all" || t.type === filter);

  return (
    <section>
      <h1 className="page-title">Admin</h1>
      {(error || cancelError) && <p className="error" role="alert">{error || cancelError}</p>}

      <h2>Users</h2>
      <div className="card table-wrap">
        {users === null ? <p className="muted">Loading…</p> : (
          <table>
            <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>MFA</th><th>Status</th></tr></thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>{u.full_name}</td>
                  <td>{u.email}</td>
                  <td><span className={`badge role-${u.role}`}>{u.role}</span></td>
                  <td>{u.mfa_enabled ? "On" : <span className="warn">Off</span>}</td>
                  <td>{u.locked ? <span className="warn">Locked</span> : "Active"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <p className="muted small">Change roles, reset MFA or unlock accounts with <code>python auth_cli.py</code>.</p>

      <div className="section-head">
        <h2>All bookings</h2>
        <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter by type">
          <option value="all">All types</option>
          <option value="flight">Flights</option>
          <option value="hotel">Hotels</option>
          <option value="car">Cars</option>
          <option value="train">Trains</option>
        </select>
      </div>
      {trips === null ? <p className="muted">Loading…</p> : <TripList trips={shown} onCancel={cancel} showOwner />}
    </section>
  );
}
