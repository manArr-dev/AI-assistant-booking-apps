import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { Details } from "../components/Offers.jsx";
import { money } from "../util.js";

const ICON = { flight: "✈", hotel: "🏨", car: "🚗", train: "🚆" };

export function TripList({ trips, onCancel, showOwner = false }) {
  if (!trips.length) return <p className="muted">No bookings yet.</p>;
  return trips.map((t) => (
    <article key={t.booking_id} className="card offer">
      <div className="offer-body">
        <div className="trip-head">
          <span className="trip-icon" aria-hidden="true">{ICON[t.type]}</span>
          <code>{t.booking_id}</code>
          <span className="muted small">ref {t.supplier_reference}</span>
          {showOwner && <span className="badge">{t.customer_email}</span>}
        </div>
        <Details kind={t.type} d={t.details} />
        <div className="muted small">Lead traveller: {t.lead_traveller}</div>
      </div>
      <div className="offer-side">
        <div className="price">{money(t.total_price, t.currency)}</div>
        <button className="btn danger" onClick={() => onCancel(t)}>Cancel</button>
      </div>
    </article>
  ));
}

export function useCancel(reload) {
  const [error, setError] = useState("");
  const cancel = useCallback(async (trip) => {
    if (!window.confirm(`Cancel ${trip.type} booking ${trip.booking_id}? Refunds depend on the fare rules.`)) return;
    setError("");
    try {
      await api.post(`/trips/${encodeURIComponent(trip.booking_id)}/cancel`);
      await reload();
    } catch (e) {
      setError(e.message);
    }
  }, [reload]);
  return [cancel, error];
}

export default function Trips() {
  const [trips, setTrips] = useState(null);
  const [loadError, setLoadError] = useState("");
  const load = useCallback(async () => {
    try {
      setTrips((await api.get("/trips")).trips);
    } catch (e) {
      setLoadError(e.message);
    }
  }, []);
  const [cancel, cancelError] = useCancel(load);
  useEffect(() => { load(); }, [load]);

  return (
    <section>
      <h1 className="page-title">My trips</h1>
      {(loadError || cancelError) && <p className="error" role="alert">{loadError || cancelError}</p>}
      {trips === null ? <p className="muted">Loading…</p> : <TripList trips={trips} onCancel={cancel} />}
    </section>
  );
}
