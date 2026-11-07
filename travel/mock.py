"""Offline sandbox providers.

Generates believable, deterministic inventory (same search -> same results)
so the full search -> book -> cancel flow works without any API key.
All companies here are fictional. Nothing is really booked.
"""
from __future__ import annotations

import hashlib
import math
import random
import uuid
from datetime import date, datetime, timedelta

from .base import TravelProvider


def _rng(*parts) -> random.Random:
    seed = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(seed[:16], 16))


def _money(x: float) -> float:
    return round(x, 2)


class _MockProvider(TravelProvider):
    name = "mock"
    ref_prefix = "MK"

    def book(self, offer: dict, details: dict) -> dict:
        return {"reference": self.ref_prefix + uuid.uuid4().hex[:6].upper(),
                "price": offer["price"], "currency": offer["currency"], "raw": {}}

    def cancel(self, booking: dict) -> dict:
        return {"refund_amount": None, "currency": None}


class MockFlights(_MockProvider):
    kind = "flight"
    ref_prefix = "PNR"
    AIRLINES = [("SA", "Skyline Air"), ("NW", "Northwind Airways"),
                ("BJ", "BlueJet"), ("CR", "Coral Airlines")]
    CABIN_FACTOR = {"economy": 1.0, "premium_economy": 1.6, "business": 3.2, "first": 5.5}

    def _leg(self, rng: random.Random, origin: str, dest: str, day: str, code: str) -> dict:
        depart = datetime.combine(date.fromisoformat(day), datetime.min.time()) + timedelta(
            hours=rng.randint(6, 21), minutes=rng.choice([0, 15, 30, 45]))
        stops = rng.choice([0, 0, 0, 1])
        arrive = depart + timedelta(minutes=rng.randint(70, 540) + stops * 90)
        return {"from": origin, "to": dest, "depart": depart.isoformat(timespec="minutes"),
                "arrive": arrive.isoformat(timespec="minutes"),
                "flight_numbers": [f"{code}{rng.randint(100, 9999)}" for _ in range(stops + 1)],
                "stops": stops}

    def search(self, q: dict) -> list[dict]:
        rng = _rng("flight", *q.values())
        offers = []
        for code, airline in rng.sample(self.AIRLINES, k=len(self.AIRLINES)):
            outbound = self._leg(rng, q["origin"], q["destination"], q["departure_date"], code)
            inbound = (self._leg(rng, q["destination"], q["origin"], q["return_date"], code)
                       if q.get("return_date") else None)
            fare = rng.uniform(60, 450) * (1.8 if inbound else 1) * self.CABIN_FACTOR[q["cabin_class"]]
            offers.append({
                "summary": {"airline": airline, "outbound": outbound, "return": inbound,
                            "cabin_class": q["cabin_class"], "passengers": q["adults"]},
                "price": _money(fare * q["adults"]), "currency": "EUR",
                "expires_at": None, "raw": {}})
        return sorted(offers, key=lambda o: o["price"])


class MockHotels(_MockProvider):
    kind = "hotel"
    ref_prefix = "HTL"
    NAMES = ["Grand {c} Hotel", "{c} Central Inn", "Harbour View {c}", "The Garden Suites {c}",
             "Riverside Lodge {c}", "{c} City Apartments"]
    ROOMS = ["Standard double", "Superior double", "Twin room", "Deluxe king", "Studio"]

    def search(self, q: dict) -> list[dict]:
        rng = _rng("hotel", *q.values())
        city = q["city"].title()
        nights = (date.fromisoformat(q["check_out"]) - date.fromisoformat(q["check_in"])).days
        offers = []
        for template in rng.sample(self.NAMES, k=5):
            stars = rng.randint(2, 5)
            per_night = rng.uniform(45, 70) * stars
            offers.append({
                "summary": {"hotel": template.format(c=city), "city": city, "stars": stars,
                            "room_type": rng.choice(self.ROOMS), "check_in": q["check_in"],
                            "check_out": q["check_out"], "nights": nights, "rooms": q["rooms"],
                            "guests": q["guests"], "price_per_night": _money(per_night * q["rooms"]),
                            "free_cancellation": rng.random() < 0.6},
                "price": _money(per_night * nights * q["rooms"]), "currency": "EUR",
                "expires_at": None, "raw": {}})
        return sorted(offers, key=lambda o: o["price"])


class MockCars(_MockProvider):
    kind = "car"
    ref_prefix = "CAR"
    SUPPLIERS = ["RoadRunner Rentals", "CityDrive", "OpenRoad Cars"]
    FLEET = [  # category, example, seats, transmission, daily rate
        ("Economy", "Hatchback or similar", 4, "manual", 28),
        ("Compact", "Compact saloon or similar", 5, "automatic", 39),
        ("SUV", "Mid-size SUV or similar", 5, "automatic", 62),
        ("Van", "7-seat people carrier or similar", 7, "manual", 75),
    ]

    def search(self, q: dict) -> list[dict]:
        rng = _rng("car", *q.values())
        start = datetime.fromisoformat(f"{q['pickup_date']}T{q['pickup_time']}")
        end = datetime.fromisoformat(f"{q['dropoff_date']}T{q['dropoff_time']}")
        days = max(1, math.ceil((end - start).total_seconds() / 86400))
        young_driver = q["driver_age"] < 25
        offers = []
        for category, example, seats, gearbox, daily in self.FLEET:
            supplier = rng.choice(self.SUPPLIERS)
            rate = daily * rng.uniform(0.85, 1.25) + (12 if young_driver else 0)
            offers.append({
                "summary": {"supplier": supplier, "category": category, "vehicle": example,
                            "seats": seats, "transmission": gearbox,
                            "pickup": f"{q['pickup_location']} {start.isoformat(timespec='minutes')}",
                            "dropoff": f"{q['dropoff_location']} {end.isoformat(timespec='minutes')}",
                            "rental_days": days, "unlimited_mileage": rng.random() < 0.7,
                            "young_driver_fee_included": young_driver},
                "price": _money(rate * days), "currency": "EUR",
                "expires_at": None, "raw": {}})
        return sorted(offers, key=lambda o: o["price"])


class MockTrains(_MockProvider):
    kind = "train"
    ref_prefix = "TRN"
    OPERATORS = [("IC", "Intercity Rail"), ("CX", "Coastal Express"), ("RL", "Regional Lines")]

    def search(self, q: dict) -> list[dict]:
        rng = _rng("train", *q.values())
        cursor = datetime.fromisoformat(f"{q['departure_date']}T{q['earliest_time']}")
        offers = []
        for _ in range(5):
            cursor += timedelta(minutes=rng.randint(25, 110))
            if cursor.date().isoformat() != q["departure_date"]:
                break
            code, operator = rng.choice(self.OPERATORS)
            changes = rng.choice([0, 0, 1])
            arrive = cursor + timedelta(minutes=rng.randint(55, 300) + changes * 20)
            fare = rng.uniform(19, 89) * (1.7 if q["travel_class"] == "first" else 1)
            offers.append({
                "summary": {"operator": operator, "train": f"{code} {rng.randint(100, 999)}",
                            "from": q["origin"], "to": q["destination"],
                            "depart": cursor.isoformat(timespec="minutes"),
                            "arrive": arrive.isoformat(timespec="minutes"),
                            "changes": changes, "travel_class": q["travel_class"],
                            "passengers": q["adults"], "seat_reservation": rng.random() < 0.8},
                "price": _money(fare * q["adults"]), "currency": "EUR",
                "expires_at": None, "raw": {}})
        return offers
