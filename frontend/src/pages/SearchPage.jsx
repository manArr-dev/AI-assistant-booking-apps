import { useState } from "react";
import { api } from "../api.js";
import BookingDialog from "../components/BookingDialog.jsx";
import { OfferCard } from "../components/Offers.jsx";
import { CarForm, FlightForm, HotelForm, TrainForm } from "../components/SearchForms.jsx";
import { money } from "../util.js";

const CONFIG = {
  flight: { form: FlightForm, path: "/search/flights", title: "Find flights" },
  hotel: { form: HotelForm, path: "/search/hotels", title: "Find hotels" },
  car: { form: CarForm, path: "/search/cars", title: "Rent a car" },
  train: { form: TrainForm, path: "/search/trains", title: "Find trains" },
};

export default function SearchPage({ kind }) {
  const { form: Form, path, title } = CONFIG[kind];
  const [offers, setOffers] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState(null);
  const [booked, setBooked] = useState(null);

  async function search(query) {
    setError("");
    setBooked(null);
    setBusy(true);
    try {
      const res = await api.post(path, query);
      setOffers(res.offers);
    } catch (e) {
      setError(e.message);
      setOffers(null);
    } finally {
      setBusy(false);
    }
  }

  function onBooked(res) {
    setBooked(res);
    setOffers((list) => list.filter((o) => o.offer_id !== selected.offer_id));
    setSelected(null);
  }

  return (
    <section>
      <h1 className="page-title">{title}</h1>
      <div className="card"><Form onSearch={search} busy={busy} /></div>

      {booked && (
        <div className="card success" role="status">
          <strong>Booked!</strong> Booking <code>{booked.booking_id}</code> · supplier reference{" "}
          <code>{booked.supplier_reference}</code> · {money(booked.total_price, booked.currency)}.
          {" "}<a href="#trips">View my trips</a>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}

      {offers && (
        <div className="results">
          <div className="muted small">
            {offers.length ? `${offers.length} option(s). Prices are totals; offers expire after about 30 minutes.`
              : "Nothing matches. Try other dates or a wider time range."}
          </div>
          {offers.map((o) => <OfferCard key={o.offer_id} kind={kind} offer={o} onBook={setSelected} />)}
        </div>
      )}

      {selected && (
        <BookingDialog kind={kind} offer={selected} onClose={() => setSelected(null)} onBooked={onBooked} />
      )}
    </section>
  );
}
