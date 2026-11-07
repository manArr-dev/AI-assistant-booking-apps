import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";
import { money, today } from "../util.js";
import { Details } from "./Offers.jsx";
import { Field } from "./SearchForms.jsx";

const blankPassenger = () => ({ title: "ms", given_name: "", family_name: "", born_on: "", gender: "f" });

export default function BookingDialog({ kind, offer, onClose, onBooked }) {
  const { user } = useAuth();
  const [first, ...rest] = user.full_name.split(" ");
  const count = offer.passengers || 1;
  const [phone, setPhone] = useState("");
  const [lead, setLead] = useState({ given_name: first, family_name: rest.join(" ") });
  const [people, setPeople] = useState(() =>
    Array.from({ length: count }, (_, i) =>
      i === 0 ? { ...blankPassenger(), given_name: first, family_name: rest.join(" ") } : blankPassenger()));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const dialog = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = busy ? () => {} : onClose;

  useEffect(() => {
    dialog.current?.querySelector("input")?.focus(); // once, when the dialog opens
    const onKey = (e) => e.key === "Escape" && closeRef.current();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const setPerson = (i, k) => (e) =>
    setPeople(people.map((p, j) => (j === i ? { ...p, [k]: e.target.value } : p)));

  async function submit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const base = { offer_id: offer.offer_id, phone };
      const body =
        kind === "flight" ? { ...base, passengers: people }
        : kind === "train" ? { ...base, passengers: people.map(({ given_name, family_name }) => ({ given_name, family_name })) }
        : { ...base, ...lead };
      onBooked(await api.post(`/book/${kind}`, body));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const leadLabel = kind === "car" ? "Main driver" : "Lead guest";

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="card dialog" role="dialog" aria-modal="true" aria-labelledby="book-title" ref={dialog}>
        <h2 id="book-title">Confirm your {kind}</h2>
        <div className="summary"><Details kind={kind} d={offer} /></div>

        <form onSubmit={submit} className="stack">
          {(kind === "flight" || kind === "train") && people.map((p, i) => (
            <fieldset key={i} className="passenger">
              <legend>Passenger {i + 1}{kind === "flight" ? " (as on passport)" : ""}</legend>
              <div className="row">
                {kind === "flight" && (
                  <Field label="Title">
                    <select value={p.title} onChange={setPerson(i, "title")}>
                      {["mr", "ms", "mrs", "miss", "dr"].map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </Field>
                )}
                <Field label="Given name"><input required value={p.given_name} onChange={setPerson(i, "given_name")} /></Field>
                <Field label="Family name"><input required value={p.family_name} onChange={setPerson(i, "family_name")} /></Field>
              </div>
              {kind === "flight" && (
                <div className="row">
                  <Field label="Date of birth">
                    <input required type="date" max={today()} value={p.born_on} onChange={setPerson(i, "born_on")} />
                  </Field>
                  <Field label="Gender">
                    <select value={p.gender} onChange={setPerson(i, "gender")}>
                      <option value="f">Female</option>
                      <option value="m">Male</option>
                    </select>
                  </Field>
                </div>
              )}
            </fieldset>
          ))}

          {(kind === "hotel" || kind === "car") && (
            <fieldset className="passenger">
              <legend>{leadLabel}</legend>
              <div className="row">
                <Field label="Given name">
                  <input required value={lead.given_name} onChange={(e) => setLead({ ...lead, given_name: e.target.value })} />
                </Field>
                <Field label="Family name">
                  <input required value={lead.family_name} onChange={(e) => setLead({ ...lead, family_name: e.target.value })} />
                </Field>
              </div>
            </fieldset>
          )}

          <Field label="Mobile phone" hint={`International format. Confirmation goes to ${user.email}.`}>
            <input required type="tel" autoComplete="tel" placeholder="+447700900123" pattern="\+[0-9 ]{7,18}"
              value={phone} onChange={(e) => setPhone(e.target.value)} />
          </Field>

          {error && <p className="error" role="alert">{error}</p>}
          <div className="dialog-actions">
            <button type="button" className="btn ghost" onClick={onClose} disabled={busy}>Cancel</button>
            <button className="btn primary" disabled={busy}>
              {busy ? "Booking…" : `Book for ${money(offer.total_price, offer.currency)}`}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
