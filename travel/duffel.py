"""Duffel API integration: flights (Duffel Flights) and hotels (Duffel Stays).

Sign up at https://duffel.com, create a *test* access token (starts with
duffel_test_) and set DUFFEL_ACCESS_TOKEN. Test mode books against the
"Duffel Airways" sandbox and pays from a fake balance, so nothing is charged.
Stays must be enabled on your Duffel account before hotel calls succeed.

API reference: https://duffel.com/docs/api/v2
"""
from __future__ import annotations

import os
import re

import httpx

import cache

from .base import ProviderError, TravelProvider

BASE_URL = "https://api.duffel.com"
COORDS_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


class DuffelClient:
    def __init__(self, token: str | None = None, transport: httpx.BaseTransport | None = None):
        token = token or os.environ.get("DUFFEL_ACCESS_TOKEN", "")
        if not token:
            raise RuntimeError("DUFFEL_ACCESS_TOKEN is not set (see .env.example).")
        self.http = httpx.Client(
            base_url=BASE_URL, timeout=httpx.Timeout(60.0, connect=10.0), transport=transport,
            headers={"Authorization": f"Bearer {token}", "Duffel-Version": "v2",
                     "Accept": "application/json", "Content-Type": "application/json"})

    def request(self, method: str, path: str, data: dict | None = None,
                params: dict | None = None) -> dict | list:
        try:
            r = self.http.request(method, path, params=params,
                                  json={"data": data} if data is not None else None)
        except httpx.HTTPError as e:
            raise ProviderError("The travel supplier could not be reached. Please try again shortly.") from e
        if r.status_code >= 400:
            raise ProviderError(_error_message(r))
        return r.json().get("data", {}) if r.content else {}


def _error_message(r: httpx.Response) -> str:
    try:
        err = r.json()["errors"][0]
        return err.get("message") or err.get("title") or f"Supplier error {r.status_code}."
    except (ValueError, KeyError, IndexError, TypeError):
        return f"Supplier error {r.status_code}."


# ---------------------------------------------------------------- flights

def _slice_summary(s: dict) -> dict:
    segs = s["segments"]
    return {
        "from": s["origin"]["iata_code"],
        "to": s["destination"]["iata_code"],
        "depart": segs[0]["departing_at"],
        "arrive": segs[-1]["arriving_at"],
        "flight_numbers": [f"{g['marketing_carrier']['iata_code']}{g['marketing_carrier_flight_number']}"
                           for g in segs],
        "stops": len(segs) - 1,
        "duration": s.get("duration"),
    }


class DuffelFlights(TravelProvider):
    name = "duffel"
    kind = "flight"
    MAX_OFFERS = 8

    def __init__(self, client: DuffelClient | None = None):
        self.client = client or DuffelClient()

    def search(self, q: dict) -> list[dict]:
        slices = [{"origin": q["origin"], "destination": q["destination"],
                   "departure_date": q["departure_date"]}]
        if q.get("return_date"):
            slices.append({"origin": q["destination"], "destination": q["origin"],
                           "departure_date": q["return_date"]})
        data = self.client.request(
            "POST", "/air/offer_requests",
            params={"return_offers": "true", "supplier_timeout": 20000},
            data={"slices": slices, "passengers": [{"type": "adult"}] * q["adults"],
                  "cabin_class": q["cabin_class"]})

        passenger_ids = [p["id"] for p in data.get("passengers", [])]
        offers = sorted(data.get("offers", []), key=lambda o: float(o["total_amount"]))
        out = []
        for o in offers[: self.MAX_OFFERS]:
            legs = [_slice_summary(s) for s in o["slices"]]
            out.append({
                "summary": {"airline": o["owner"]["name"], "outbound": legs[0],
                            "return": legs[1] if len(legs) > 1 else None,
                            "cabin_class": q["cabin_class"], "passengers": q["adults"]},
                "price": float(o["total_amount"]),
                "currency": o["total_currency"],
                "expires_at": o.get("expires_at"),
                "raw": {"offer_id": o["id"], "passenger_ids": passenger_ids,
                        "total_amount": o["total_amount"], "total_currency": o["total_currency"]},
            })
        return out

    def book(self, offer: dict, details: dict) -> dict:
        raw = offer["raw"]
        if len(details["passengers"]) != len(raw["passenger_ids"]):
            raise ProviderError(f"This offer needs details for {len(raw['passenger_ids'])} passenger(s).")
        passengers = [
            {"id": pid, "title": p["title"], "given_name": p["given_name"],
             "family_name": p["family_name"], "born_on": p["born_on"], "gender": p["gender"],
             "email": details["email"], "phone_number": details["phone"]}
            for pid, p in zip(raw["passenger_ids"], details["passengers"], strict=True)
        ]
        order = self.client.request("POST", "/air/orders", data={
            "type": "instant",
            "selected_offers": [raw["offer_id"]],
            "passengers": passengers,
            # Pays from your Duffel balance (fake money in test mode). Amount must equal the offer total.
            "payments": [{"type": "balance", "currency": raw["total_currency"],
                          "amount": raw["total_amount"]}],
        })
        return {"reference": order["booking_reference"], "price": float(order["total_amount"]),
                "currency": order["total_currency"], "raw": {"order_id": order["id"]}}

    def cancel(self, booking: dict) -> dict:
        pending = self.client.request("POST", "/air/order_cancellations",
                                      data={"order_id": booking["raw"]["order_id"]})
        done = self.client.request("POST", f"/air/order_cancellations/{pending['id']}/actions/confirm")
        amount = done.get("refund_amount")
        return {"refund_amount": float(amount) if amount is not None else None,
                "currency": done.get("refund_currency")}


# ---------------------------------------------------------------- hotels

class DuffelHotels(TravelProvider):
    name = "duffel"
    kind = "hotel"
    MAX_OFFERS = 8
    RADIUS_KM = 5

    def __init__(self, client: DuffelClient | None = None):
        self.client = client or DuffelClient()

    def _coordinates(self, place: str) -> dict:
        if m := COORDS_RE.match(place):
            return {"latitude": float(m.group(1)), "longitude": float(m.group(2))}
        return cache.cached("geo", 30 * 86400, (place.strip().lower(),), lambda: self._lookup(place))

    def _lookup(self, place: str) -> dict:
        for p in self.client.request("GET", "/places/suggestions", params={"query": place}) or []:
            candidates = [p] + (p.get("airports") or [])
            for c in candidates:
                if c.get("latitude") is not None and c.get("longitude") is not None:
                    return {"latitude": float(c["latitude"]), "longitude": float(c["longitude"])}
        raise ProviderError(f"Could not find a location called '{place}'. Try a larger nearby city.")

    def search(self, q: dict) -> list[dict]:
        data = self.client.request("POST", "/stays/search", data={
            "location": {"radius": self.RADIUS_KM, "geographic_coordinates": self._coordinates(q["city"])},
            "check_in_date": q["check_in"],
            "check_out_date": q["check_out"],
            "rooms": q["rooms"],
            "guests": [{"type": "adult"}] * q["guests"],
        })
        results = sorted(data.get("results", []), key=lambda r: float(r["cheapest_rate_total_amount"]))
        out = []
        for r in results[: self.MAX_OFFERS]:
            acc = r.get("accommodation", {})
            address = (acc.get("location") or {}).get("address") or {}
            out.append({
                "summary": {"hotel": acc.get("name"), "stars": acc.get("rating"),
                            "review_score": acc.get("review_score"),
                            "address": ", ".join(filter(None, [address.get("line_one"),
                                                               address.get("city_name")])),
                            "check_in": q["check_in"], "check_out": q["check_out"],
                            "rooms": q["rooms"], "guests": q["guests"],
                            "note": "Cheapest available rate; room type confirmed at booking."},
                "price": float(r["cheapest_rate_total_amount"]),
                "currency": r["cheapest_rate_currency"],
                "expires_at": r.get("expires_at"),
                "raw": {"search_result_id": r["id"]},
            })
        return out

    def book(self, offer: dict, details: dict) -> dict:
        result = self.client.request(
            "POST", f"/stays/search_results/{offer['raw']['search_result_id']}/actions/fetch_all_rates")
        rates = [rate for room in result["accommodation"]["rooms"] for rate in room["rates"]]
        if not rates:
            raise ProviderError("That hotel has no rooms left for these dates. Please search again.")
        cheapest = min(rates, key=lambda rate: float(rate["total_amount"]))

        quote = self.client.request("POST", "/stays/quotes", data={"rate_id": cheapest["id"]})
        if float(quote["total_amount"]) > offer["price"] + 0.01:
            raise ProviderError(f"The price has gone up to {quote['total_amount']} "
                                f"{quote['total_currency']}. Please search again to confirm.")

        booking = self.client.request("POST", "/stays/bookings", data={
            "quote_id": quote["id"],
            "guests": [{"given_name": details["given_name"], "family_name": details["family_name"]}],
            "email": details["email"],
            "phone_number": details["phone"],
        })
        return {"reference": booking.get("reference") or booking["id"],
                "price": float(quote["total_amount"]), "currency": quote["total_currency"],
                "raw": {"booking_id": booking["id"]}}

    def cancel(self, booking: dict) -> dict:
        self.client.request("POST", f"/stays/bookings/{booking['raw']['booking_id']}/actions/cancel")
        return {"refund_amount": None, "currency": None}
