import { duration, hhmm, money, niceDate } from "../util.js";

/** "Faro 2026-11-20T09:30" -> "Faro, Fri, Nov 20 09:30" */
const whenWhere = (s = "") =>
  s.replace(/\s*(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/, (_, d, t) => `, ${niceDate(d)} ${t}`);

function Leg({ leg, label }) {
  return (
    <div className="leg">
      {label && <span className="leg-label">{label}</span>}
      <div className="leg-times">
        <strong>{hhmm(leg.depart)}</strong>
        <span className="leg-line" aria-hidden="true">
          <span>{duration(leg.depart, leg.arrive)}</span>
        </span>
        <strong>{hhmm(leg.arrive)}</strong>
      </div>
      <div className="leg-meta muted">
        {leg.from} → {leg.to} · {niceDate(leg.depart)} ·{" "}
        {leg.stops === 0 ? "Direct" : `${leg.stops} stop${leg.stops > 1 ? "s" : ""}`}
        {leg.flight_numbers && ` · ${leg.flight_numbers.join(", ")}`}
      </div>
    </div>
  );
}

/** Main details of an offer or a booked trip, per kind. */
export function Details({ kind, d }) {
  if (kind === "flight") {
    return (
      <>
        <div className="title">{d.airline}</div>
        <Leg leg={d.outbound} label={d.return ? "Outbound" : null} />
        {d.return && <Leg leg={d.return} label="Return" />}
        <div className="muted small">{d.passengers} passenger(s) · {d.cabin_class.replace("_", " ")}</div>
      </>
    );
  }
  if (kind === "hotel") {
    return (
      <>
        <div className="title">
          {d.hotel} {d.stars ? <span className="stars" aria-label={`${d.stars} stars`}>{"★".repeat(d.stars)}</span> : null}
        </div>
        <div className="muted">{[d.room_type, d.address || d.city].filter(Boolean).join(" · ")}</div>
        <div className="muted small">
          {niceDate(d.check_in)} → {niceDate(d.check_out)}
          {d.nights ? ` · ${d.nights} night(s)` : ""} · {d.guests} guest(s), {d.rooms} room(s)
        </div>
        {d.free_cancellation && <span className="tag good">Free cancellation</span>}
      </>
    );
  }
  if (kind === "car") {
    return (
      <>
        <div className="title">{d.category} · {d.vehicle}</div>
        <div className="muted">{d.supplier} · {d.seats} seats · {d.transmission}</div>
        <div className="muted small">{whenWhere(d.pickup)} → {whenWhere(d.dropoff)} · {d.rental_days} day(s)</div>
        {d.unlimited_mileage && <span className="tag good">Unlimited mileage</span>}
      </>
    );
  }
  return (
    <>
      <div className="title">{d.operator} <span className="muted">{d.train}</span></div>
      <Leg leg={{ ...d, stops: d.changes, flight_numbers: null }} />
      <div className="muted small">
        {d.passengers} passenger(s) · {d.travel_class} class{d.seat_reservation ? " · seat reserved" : ""}
      </div>
    </>
  );
}

export function OfferCard({ kind, offer, onBook }) {
  return (
    <article className="card offer">
      <div className="offer-body"><Details kind={kind} d={offer} /></div>
      <div className="offer-side">
        <div className="price">{money(offer.total_price, offer.currency)}</div>
        {kind === "hotel" && offer.price_per_night && (
          <div className="muted small">{money(offer.price_per_night, offer.currency)} / night</div>
        )}
        <div className="muted small">total</div>
        <button className="btn primary" onClick={() => onBook(offer)}>Book</button>
      </div>
    </article>
  );
}
