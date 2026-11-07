import { useState } from "react";
import { addDays, AIRPORTS, CITIES, TIME_PRESETS, today } from "../util.js";

export function Field({ label, hint, children, wide = false }) {
  return (
    <label className={`field ${wide ? "wide" : ""}`}>
      {label}
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

function Lists() {
  return (
    <>
      <datalist id="airports">
        {AIRPORTS.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
      </datalist>
      <datalist id="cities">{CITIES.map((c) => <option key={c} value={c} />)}</datalist>
    </>
  );
}

/** "Depart between [time] and [time]" with quick presets. */
function TimeRange({ after, before, onChange }) {
  return (
    <fieldset className="time-range wide">
      <legend>Departure time</legend>
      <div className="chips">
        {TIME_PRESETS.map((p) => (
          <button type="button" key={p.label}
            className={`chip ${p.after === after && p.before === before ? "on" : ""}`}
            onClick={() => onChange(p.after, p.before)}>{p.label}</button>
        ))}
      </div>
      <div className="row">
        <Field label="Earliest">
          <input type="time" value={after} onChange={(e) => onChange(e.target.value, before)} />
        </Field>
        <Field label="Latest">
          <input type="time" value={before} onChange={(e) => onChange(after, e.target.value)} />
        </Field>
      </div>
    </fieldset>
  );
}

function SearchButton({ busy }) {
  return (
    <div className="form-actions wide">
      <button className="btn primary" disabled={busy}>{busy ? "Searching…" : "Search"}</button>
    </div>
  );
}

function useForm(initial) {
  const [v, setV] = useState(initial);
  const bind = (k, transform = (x) => x) => ({
    value: v[k],
    onChange: (e) => setV({ ...v, [k]: transform(e.target.type === "number" ? Number(e.target.value) : e.target.value) }),
  });
  return [v, setV, bind];
}

const upper3 = (s) => s.toUpperCase().replace(/[^A-Z]/g, "").slice(0, 3);

export function FlightForm({ onSearch, busy }) {
  const [v, setV, bind] = useForm({
    origin: "", destination: "", trip: "return", departure_date: addDays(14), return_date: addDays(21),
    adults: 1, cabin_class: "economy", depart_after: "", depart_before: "",
  });
  const submit = (e) => {
    e.preventDefault();
    const { trip, ...q } = v;
    onSearch({ ...q, return_date: trip === "return" ? v.return_date : "" });
  };
  return (
    <form className="search-form" onSubmit={submit}>
      <Lists />
      <div className="segmented wide">
        {["return", "oneway"].map((t) => (
          <button type="button" key={t} className={v.trip === t ? "on" : ""} onClick={() => setV({ ...v, trip: t })}>
            {t === "return" ? "Return" : "One way"}
          </button>
        ))}
      </div>
      <Field label="From" hint="Airport or city code, e.g. LHR">
        <input required list="airports" placeholder="LHR" pattern="[A-Z]{3}" {...bind("origin", upper3)} />
      </Field>
      <Field label="To" hint="e.g. LIS">
        <input required list="airports" placeholder="LIS" pattern="[A-Z]{3}" {...bind("destination", upper3)} />
      </Field>
      <Field label="Depart">
        <input required type="date" min={today()} {...bind("departure_date")} />
      </Field>
      {v.trip === "return" && (
        <Field label="Return">
          <input required type="date" min={v.departure_date} {...bind("return_date")} />
        </Field>
      )}
      <Field label="Passengers">
        <input type="number" min="1" max="9" {...bind("adults")} />
      </Field>
      <Field label="Cabin">
        <select {...bind("cabin_class")}>
          <option value="economy">Economy</option>
          <option value="premium_economy">Premium economy</option>
          <option value="business">Business</option>
          <option value="first">First</option>
        </select>
      </Field>
      <TimeRange after={v.depart_after} before={v.depart_before}
        onChange={(a, b) => setV({ ...v, depart_after: a, depart_before: b })} />
      <SearchButton busy={busy} />
    </form>
  );
}

export function HotelForm({ onSearch, busy }) {
  const [v, , bind] = useForm({ city: "", check_in: addDays(14), check_out: addDays(17), guests: 2, rooms: 1 });
  return (
    <form className="search-form" onSubmit={(e) => { e.preventDefault(); onSearch(v); }}>
      <Lists />
      <Field label="Destination" wide>
        <input required list="cities" placeholder="City, e.g. Lisbon" {...bind("city")} />
      </Field>
      <Field label="Check-in">
        <input required type="date" min={today()} {...bind("check_in")} />
      </Field>
      <Field label="Check-out">
        <input required type="date" min={addDays(1, new Date(`${v.check_in}T12:00`))} {...bind("check_out")} />
      </Field>
      <Field label="Guests">
        <input type="number" min="1" max="9" {...bind("guests")} />
      </Field>
      <Field label="Rooms">
        <input type="number" min="1" max="5" {...bind("rooms")} />
      </Field>
      <SearchButton busy={busy} />
    </form>
  );
}

export function CarForm({ onSearch, busy }) {
  const [v, setV, bind] = useForm({
    pickup_location: "", dropoff_location: "", different_return: false,
    pickup_date: addDays(14), pickup_time: "10:00", dropoff_date: addDays(21), dropoff_time: "10:00", driver_age: 30,
  });
  const submit = (e) => {
    e.preventDefault();
    const { different_return, ...q } = v;
    onSearch({ ...q, dropoff_location: different_return ? v.dropoff_location : "" });
  };
  return (
    <form className="search-form" onSubmit={submit}>
      <Lists />
      <Field label="Pick-up location" wide>
        <input required list="cities" placeholder="Airport or city, e.g. Faro" {...bind("pickup_location")} />
      </Field>
      <label className="check wide">
        <input type="checkbox" checked={v.different_return}
          onChange={(e) => setV({ ...v, different_return: e.target.checked })} />
        Return the car to a different location
      </label>
      {v.different_return && (
        <Field label="Drop-off location" wide>
          <input required list="cities" {...bind("dropoff_location")} />
        </Field>
      )}
      <Field label="Pick-up date">
        <input required type="date" min={today()} {...bind("pickup_date")} />
      </Field>
      <Field label="Pick-up time">
        <input required type="time" {...bind("pickup_time")} />
      </Field>
      <Field label="Drop-off date">
        <input required type="date" min={v.pickup_date} {...bind("dropoff_date")} />
      </Field>
      <Field label="Drop-off time">
        <input required type="time" {...bind("dropoff_time")} />
      </Field>
      <Field label="Driver age" hint="Under 25 may add a fee">
        <input type="number" min="18" max="99" {...bind("driver_age")} />
      </Field>
      <SearchButton busy={busy} />
    </form>
  );
}

export function TrainForm({ onSearch, busy }) {
  const [v, setV, bind] = useForm({
    origin: "", destination: "", departure_date: addDays(7), adults: 1, travel_class: "standard",
    depart_after: "06:00", depart_before: "",
  });
  return (
    <form className="search-form" onSubmit={(e) => { e.preventDefault(); onSearch(v); }}>
      <Lists />
      <Field label="From">
        <input required list="cities" placeholder="Station or city" {...bind("origin")} />
      </Field>
      <Field label="To">
        <input required list="cities" placeholder="Station or city" {...bind("destination")} />
      </Field>
      <Field label="Date">
        <input required type="date" min={today()} {...bind("departure_date")} />
      </Field>
      <Field label="Passengers">
        <input type="number" min="1" max="9" {...bind("adults")} />
      </Field>
      <Field label="Class">
        <select {...bind("travel_class")}>
          <option value="standard">Standard</option>
          <option value="first">First</option>
        </select>
      </Field>
      <TimeRange after={v.depart_after} before={v.depart_before}
        onChange={(a, b) => setV({ ...v, depart_after: a, depart_before: b })} />
      <SearchButton busy={busy} />
    </form>
  );
}
