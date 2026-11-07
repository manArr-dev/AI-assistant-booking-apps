// Date/time helpers and suggestion lists used across the forms.

export function isoDate(d) {
  const local = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

export function addDays(n, from = new Date()) {
  const d = new Date(from);
  d.setDate(d.getDate() + n);
  return isoDate(d);
}

export const today = () => isoDate(new Date());

export const hhmm = (iso) => (iso ? iso.slice(11, 16) : "");

export function niceDate(iso) {
  if (!iso) return "";
  const d = new Date(`${iso.slice(0, 10)}T12:00:00`);
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

export function duration(fromIso, toIso) {
  const mins = Math.round((new Date(toIso) - new Date(fromIso)) / 60000);
  if (!Number.isFinite(mins) || mins <= 0) return "";
  return `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, "0")}m`;
}

export function money(amount, currency) {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(amount);
  } catch {
    return `${amount} ${currency}`;
  }
}

export const TIME_PRESETS = [
  { label: "Any time", after: "", before: "" },
  { label: "Morning", after: "05:00", before: "11:59" },
  { label: "Afternoon", after: "12:00", before: "17:59" },
  { label: "Evening", after: "18:00", before: "23:59" },
];

export const AIRPORTS = [
  ["LON", "London (all airports)"], ["LHR", "London Heathrow"], ["LGW", "London Gatwick"],
  ["MAN", "Manchester"], ["DUB", "Dublin"], ["PAR", "Paris (all airports)"], ["CDG", "Paris Charles de Gaulle"],
  ["AMS", "Amsterdam"], ["FRA", "Frankfurt"], ["MAD", "Madrid"], ["BCN", "Barcelona"],
  ["LIS", "Lisbon"], ["OPO", "Porto"], ["FAO", "Faro"], ["FCO", "Rome Fiumicino"], ["MXP", "Milan Malpensa"],
  ["ATH", "Athens"], ["IST", "Istanbul"], ["DXB", "Dubai"], ["NYC", "New York (all airports)"],
  ["JFK", "New York JFK"], ["LAX", "Los Angeles"], ["SFO", "San Francisco"], ["YYZ", "Toronto"],
  ["NBO", "Nairobi"], ["JNB", "Johannesburg"], ["SIN", "Singapore"], ["HND", "Tokyo Haneda"],
];

export const CITIES = [
  "London", "Manchester", "Edinburgh", "Dublin", "Paris", "Lyon", "Amsterdam", "Brussels", "Berlin",
  "Munich", "Frankfurt", "Zurich", "Vienna", "Madrid", "Barcelona", "Lisbon", "Porto", "Faro", "Rome",
  "Milan", "Florence", "Athens", "Istanbul", "New York", "Nairobi", "Mombasa", "Cape Town", "Tokyo",
];
