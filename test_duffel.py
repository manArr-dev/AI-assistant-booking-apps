"""
Check the Duffel integration's request/response mapping against a fake HTTP
transport, so it runs offline with no token. Payloads follow the shapes in
https://duffel.com/docs/api/v2.

    python test_duffel.py

To try the real API instead: put a duffel_test_ token in .env, set
FLIGHT_PROVIDER=duffel and run python agent.py.
"""
import json

import httpx

from travel.base import ProviderError
from travel.duffel import DuffelClient, DuffelFlights, DuffelHotels

calls = []


def segment(origin, dest, dep, arr, num):
    return {"departing_at": dep, "arriving_at": arr, "marketing_carrier": {"iata_code": "ZZ"},
            "marketing_carrier_flight_number": num, "origin": {"iata_code": origin},
            "destination": {"iata_code": dest}}


def handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content) if request.content else None
    calls.append((request.method, request.url.path, body))
    assert request.headers["Authorization"] == "Bearer duffel_test_x"
    assert request.headers["Duffel-Version"] == "v2"
    path = request.url.path

    if path == "/air/offer_requests":
        assert request.url.params["return_offers"] == "true"
        return httpx.Response(201, json={"data": {
            "passengers": [{"id": "pas_1"}, {"id": "pas_2"}],
            "offers": [
                {"id": "off_exp", "total_amount": "900.00", "total_currency": "GBP",
                 "owner": {"name": "Duffel Airways"}, "expires_at": "2099-01-01T00:00:00Z",
                 "slices": [{"origin": {"iata_code": "LHR"}, "destination": {"iata_code": "JFK"},
                             "duration": "PT8H", "segments": [segment("LHR", "JFK", "a", "b", "1")]}]},
                {"id": "off_cheap", "total_amount": "412.50", "total_currency": "GBP",
                 "owner": {"name": "Duffel Airways"}, "expires_at": "2099-01-01T00:00:00Z",
                 "slices": [{"origin": {"iata_code": "LHR"}, "destination": {"iata_code": "JFK"},
                             "duration": "PT10H",
                             "segments": [segment("LHR", "DUB", "2026-12-01T08:00:00", "x", "10"),
                                          segment("DUB", "JFK", "y", "2026-12-01T15:00:00", "11")]}]},
            ]}})
    if path == "/air/orders":
        d = body["data"]
        if d["selected_offers"] != ["off_cheap"]:
            return httpx.Response(422, json={"errors": [{"title": "Offer gone",
                                                         "message": "Offer no longer available"}]})
        assert d["type"] == "instant"
        assert [p["id"] for p in d["passengers"]] == ["pas_1", "pas_2"]
        assert d["payments"] == [{"type": "balance", "currency": "GBP", "amount": "412.50"}]
        return httpx.Response(201, json={"data": {"id": "ord_1", "booking_reference": "RZPNX8",
                                                  "total_amount": "412.50", "total_currency": "GBP"}})
    if path == "/air/order_cancellations":
        assert body["data"] == {"order_id": "ord_1"}
        return httpx.Response(201, json={"data": {"id": "ore_1"}})
    if path == "/air/order_cancellations/ore_1/actions/confirm":
        return httpx.Response(200, json={"data": {"refund_amount": "300.00", "refund_currency": "GBP"}})

    if path == "/places/suggestions":
        return httpx.Response(200, json={"data": [{"type": "city", "name": "London",
                                                    "airports": [{"latitude": 51.47, "longitude": -0.45}]}]})
    if path == "/stays/search":
        loc = body["data"]["location"]["geographic_coordinates"]
        assert loc == {"latitude": 51.47, "longitude": -0.45}
        assert len(body["data"]["guests"]) == 2
        return httpx.Response(200, json={"data": {"results": [
            {"id": "srr_1", "cheapest_rate_total_amount": "350.00", "cheapest_rate_currency": "GBP",
             "accommodation": {"name": "Test Hotel", "rating": 4,
                               "location": {"address": {"line_one": "1 High St", "city_name": "London"}}}}]}})
    if path == "/stays/search_results/srr_1/actions/fetch_all_rates":
        return httpx.Response(200, json={"data": {"accommodation": {"rooms": [
            {"rates": [{"id": "rat_hi", "total_amount": "420.00"}, {"id": "rat_lo", "total_amount": "350.00"}]}]}}})
    if path == "/stays/quotes":
        assert body["data"] == {"rate_id": "rat_lo"}
        return httpx.Response(201, json={"data": {"id": "quo_1", "total_amount": "350.00",
                                                  "total_currency": "GBP"}})
    if path == "/stays/bookings":
        assert body["data"]["quote_id"] == "quo_1"
        return httpx.Response(201, json={"data": {"id": "bok_1", "reference": "H12345"}})
    if path == "/stays/bookings/bok_1/actions/cancel":
        return httpx.Response(200, json={"data": {"id": "bok_1", "status": "cancelled"}})

    return httpx.Response(422, json={"errors": [{"title": "Bad", "message": "Offer no longer available"}]})


def main():
    client = DuffelClient(token="duffel_test_x", transport=httpx.MockTransport(handler))

    flights = DuffelFlights(client)
    offers = flights.search({"origin": "LHR", "destination": "JFK", "departure_date": "2026-12-01",
                             "return_date": "", "adults": 2, "cabin_class": "economy"})
    assert [o["raw"]["offer_id"] for o in offers] == ["off_cheap", "off_exp"], "sorted by price"
    assert offers[0]["summary"]["outbound"]["stops"] == 1
    assert offers[0]["summary"]["outbound"]["flight_numbers"] == ["ZZ10", "ZZ11"]
    pax = {"title": "ms", "given_name": "Ada", "family_name": "L", "born_on": "1990-01-01", "gender": "f"}
    booked = flights.book(offers[0], {"passengers": [pax, pax], "email": "a@b.co", "phone": "+447700900123"})
    assert booked["reference"] == "RZPNX8" and booked["raw"] == {"order_id": "ord_1"}
    refund = flights.cancel({"reference": "RZPNX8", "raw": booked["raw"]})
    assert refund == {"refund_amount": 300.0, "currency": "GBP"}

    try:
        flights.book({**offers[1], "raw": {**offers[1]["raw"], "offer_id": "off_gone"}},
                     {"passengers": [pax, pax], "email": "a@b.co", "phone": "+447700900123"})
    except ProviderError as e:
        assert "no longer available" in str(e)
    else:
        raise AssertionError("API errors should surface as ProviderError")

    hotels = DuffelHotels(client)
    h = hotels.search({"city": "London", "check_in": "2026-12-01", "check_out": "2026-12-04",
                       "guests": 2, "rooms": 1})
    assert h[0]["summary"]["hotel"] == "Test Hotel" and h[0]["price"] == 350.0
    hb = hotels.book(h[0], {"given_name": "Ada", "family_name": "L", "email": "a@b.co",
                            "phone": "+447700900123"})
    assert hb["reference"] == "H12345" and hb["raw"] == {"booking_id": "bok_1"}
    hotels.cancel({"reference": "H12345", "raw": hb["raw"]})

    for method, path, _ in calls:
        print(method, path)
    print("\nAll Duffel mapping checks passed.")


if __name__ == "__main__":
    main()
