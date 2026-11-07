"""Travel booking rules, in plain Python, not in the LLM prompt.

Validates every search and booking before any supplier API is called, and
returns {"ok": False, "error": ...} with a customer-safe message on failure,
the same convention as db.py.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

import cache
from db import EMAIL_RE

from . import store
from .base import ProviderError
from .registry import get_provider

log = logging.getLogger("travel")

IATA_RE = re.compile(r"^[A-Z]{3}$")
PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")  # E.164, e.g. +442080160509
MAX_DAYS_AHEAD = 330
MAX_TRAVELLERS = 9
MAX_NIGHTS = 30
MAX_RENTAL_DAYS = 30
CABIN_CLASSES = {"economy", "premium_economy", "business", "first"}
TRAVEL_CLASSES = {"standard", "first"}
TITLES = {"mr", "ms", "mrs", "miss", "dr"}
GENDERS = {"m", "f"}


class Invalid(ValueError):
    pass


# ---------------------------------------------------------------- validators

def _future_date(value: str, field: str) -> date:
    try:
        d = date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        raise Invalid(f"{field} must be a date in YYYY-MM-DD format.") from None
    if d < date.today():
        raise Invalid(f"{field} is in the past.")
    if d > date.today() + timedelta(days=MAX_DAYS_AHEAD):
        raise Invalid(f"{field} is too far ahead (max {MAX_DAYS_AHEAD} days).")
    return d


def _time(value: str, field: str) -> str:
    try:
        return datetime.strptime(value.strip(), "%H:%M").strftime("%H:%M")
    except (ValueError, AttributeError):
        raise Invalid(f"{field} must be a 24-hour time in HH:MM format.") from None


def _iata(value: str, field: str) -> str:
    code = (value or "").strip().upper()
    if not IATA_RE.match(code):
        raise Invalid(f"{field} must be a 3-letter IATA airport or city code, e.g. LHR or NYC.")
    return code


def _place(value: str, field: str) -> str:
    text = " ".join((value or "").split())
    if not 2 <= len(text) <= 100:
        raise Invalid(f"{field} is required.")
    return text


def _count(value: int, field: str, low: int = 1, high: int = MAX_TRAVELLERS) -> int:
    if not isinstance(value, int) or not low <= value <= high:
        raise Invalid(f"{field} must be between {low} and {high}.")
    return value


def _name(value: str, field: str) -> str:
    text = " ".join((value or "").split())
    if not text or len(text) > 60:
        raise Invalid(f"{field} is required.")
    return text


def _contact(email: str, phone: str) -> tuple[str, str]:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise Invalid("A valid email address is required.")
    phone = re.sub(r"[\s\-()]", "", phone or "")
    if not PHONE_RE.match(phone):
        raise Invalid("A phone number in international format is required, e.g. +447700900123.")
    return email, phone


def _passenger(p: dict, i: int) -> dict:
    label = f"Passenger {i}"
    title = (p.get("title") or "").strip().lower().rstrip(".")
    gender = (p.get("gender") or "").strip().lower()[:1]
    if title not in TITLES:
        raise Invalid(f"{label}: title must be one of {', '.join(sorted(TITLES))}.")
    if gender not in GENDERS:
        raise Invalid(f"{label}: gender must be m or f (as on the passport).")
    try:
        born = date.fromisoformat((p.get("born_on") or "").strip())
    except ValueError:
        raise Invalid(f"{label}: date of birth must be YYYY-MM-DD.") from None
    if born >= date.today() or born.year < 1900:
        raise Invalid(f"{label}: date of birth is not valid.")
    return {"title": title, "gender": gender, "born_on": born.isoformat(),
            "given_name": _name(p.get("given_name"), f"{label} given name"),
            "family_name": _name(p.get("family_name"), f"{label} family name")}


def _fail(e: Exception) -> dict:
    return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------- search

def _search(kind: str, query: dict, user_id: int | None) -> dict:
    provider = get_provider(kind)
    try:
        # Identical searches within SEARCH_CACHE_TTL reuse the supplier's answer (Redis).
        # Each user still gets their own offer ids from store.save_offers below.
        offers = cache.cached(f"search:{kind}:{provider.name}", cache.search_ttl(),
                              (query,), lambda: provider.search(query))
    except ProviderError as e:
        return _fail(e)
    except Exception:
        log.exception("%s search failed", kind)
        return {"ok": False, "error": f"The {kind} search failed. Please try again."}
    if not offers:
        return {"ok": True, "offers": [], "message": "Nothing available for that search."}
    return {"ok": True, "provider": provider.name, "offers": store.save_offers(kind, provider.name, offers, user_id),
            "note": "Prices are totals for the whole party. Offers expire after about 30 minutes."}


def search_flights(origin: str, destination: str, departure_date: str, return_date: str = "",
                   adults: int = 1, cabin_class: str = "economy", user_id: int | None = None) -> dict:
    try:
        q = {"origin": _iata(origin, "origin"), "destination": _iata(destination, "destination"),
             "departure_date": _future_date(departure_date, "departure_date").isoformat(),
             "return_date": "", "adults": _count(adults, "adults"),
             "cabin_class": (cabin_class or "economy").strip().lower()}
        if q["origin"] == q["destination"]:
            raise Invalid("Origin and destination must be different.")
        if q["cabin_class"] not in CABIN_CLASSES:
            raise Invalid(f"cabin_class must be one of {', '.join(sorted(CABIN_CLASSES))}.")
        if return_date and return_date.strip():
            ret = _future_date(return_date, "return_date")
            if ret < date.fromisoformat(q["departure_date"]):
                raise Invalid("return_date must be on or after departure_date.")
            q["return_date"] = ret.isoformat()
    except Invalid as e:
        return _fail(e)
    return _search("flight", q, user_id)


def search_hotels(city: str, check_in: str, check_out: str, guests: int = 1, rooms: int = 1,
                  user_id: int | None = None) -> dict:
    try:
        cin = _future_date(check_in, "check_in")
        cout = _future_date(check_out, "check_out")
        nights = (cout - cin).days
        if nights < 1:
            raise Invalid("check_out must be after check_in.")
        if nights > MAX_NIGHTS:
            raise Invalid(f"Stays are limited to {MAX_NIGHTS} nights.")
        rooms = _count(rooms, "rooms", high=5)
        guests = _count(guests, "guests")
        if guests < rooms:
            raise Invalid("Each room needs at least one guest.")
        q = {"city": _place(city, "city"), "check_in": cin.isoformat(), "check_out": cout.isoformat(),
             "guests": guests, "rooms": rooms}
    except Invalid as e:
        return _fail(e)
    return _search("hotel", q, user_id)


def search_cars(pickup_location: str, pickup_date: str, pickup_time: str, dropoff_date: str,
                dropoff_time: str, dropoff_location: str = "", driver_age: int = 30,
                user_id: int | None = None) -> dict:
    try:
        pickup = datetime.combine(_future_date(pickup_date, "pickup_date"),
                                  datetime.strptime(_time(pickup_time, "pickup_time"), "%H:%M").time())
        dropoff = datetime.combine(_future_date(dropoff_date, "dropoff_date"),
                                   datetime.strptime(_time(dropoff_time, "dropoff_time"), "%H:%M").time())
        if pickup <= datetime.now() + timedelta(hours=2):
            raise Invalid("Pick-up must be at least 2 hours from now.")
        if dropoff <= pickup:
            raise Invalid("Drop-off must be after pick-up.")
        if dropoff - pickup > timedelta(days=MAX_RENTAL_DAYS):
            raise Invalid(f"Rentals are limited to {MAX_RENTAL_DAYS} days.")
        location = _place(pickup_location, "pickup_location")
        q = {"pickup_location": location,
             "dropoff_location": _place(dropoff_location, "dropoff_location")
             if dropoff_location and dropoff_location.strip() else location,
             "pickup_date": pickup.date().isoformat(), "pickup_time": pickup.strftime("%H:%M"),
             "dropoff_date": dropoff.date().isoformat(), "dropoff_time": dropoff.strftime("%H:%M"),
             "driver_age": _count(driver_age, "driver_age", 18, 99)}
    except Invalid as e:
        return _fail(e)
    return _search("car", q, user_id)


def search_trains(origin: str, destination: str, departure_date: str, earliest_time: str = "06:00",
                  adults: int = 1, travel_class: str = "standard", user_id: int | None = None) -> dict:
    try:
        q = {"origin": _place(origin, "origin"), "destination": _place(destination, "destination"),
             "departure_date": _future_date(departure_date, "departure_date").isoformat(),
             "earliest_time": _time(earliest_time or "06:00", "earliest_time"),
             "adults": _count(adults, "adults"),
             "travel_class": (travel_class or "standard").strip().lower()}
        if q["origin"].lower() == q["destination"].lower():
            raise Invalid("Origin and destination must be different.")
        if q["travel_class"] not in TRAVEL_CLASSES:
            raise Invalid("travel_class must be standard or first.")
    except Invalid as e:
        return _fail(e)
    return _search("train", q, user_id)


# ---------------------------------------------------------------- booking

def _own_offer(offer_id: str, kind: str, user_id: int | None) -> dict | None:
    """The offer, only if it is this kind and was found by this user's own search."""
    offer = store.get_offer(offer_id or "")
    if not offer or offer["kind"] != kind or offer["user_id"] != user_id:
        return None
    return offer


def _book(kind: str, offer_id: str, email: str, lead_traveller: str, details: dict,
          user_id: int | None) -> dict:
    offer = _own_offer(offer_id, kind, user_id)
    if not offer:
        return {"ok": False, "error": f"Unknown {kind} offer id. Use an offer_id from your latest search."}
    if offer["expired"]:
        return {"ok": False, "error": "That offer has expired. Please search again."}
    provider = get_provider(kind)
    if provider.name != offer["provider"]:
        return {"ok": False, "error": "The supplier configuration changed. Please search again."}
    if not store.claim_offer(offer["id"]):
        return {"ok": False, "error": "That offer has already been booked. Search again for a new one."}

    try:
        result = provider.book(offer, details)
    except ProviderError as e:
        store.release_offer(offer["id"])
        return _fail(e)
    except Exception:
        store.release_offer(offer["id"])
        log.exception("%s booking failed for offer %s", kind, offer["id"])
        return {"ok": False, "error": f"The {kind} booking could not be completed. Nothing was booked."}

    booking_id = store.save_booking(offer, result, email, lead_traveller)
    return {"ok": True, "booking_id": booking_id, "type": kind, "supplier": provider.name,
            "supplier_reference": result["reference"], "details": offer["summary"],
            "total_price": result["price"], "currency": result["currency"]}


def book_flight(offer_id: str, passengers: list[dict], email: str, phone: str,
                user_id: int | None = None) -> dict:
    try:
        email, phone = _contact(email, phone)
        if not passengers:
            raise Invalid("Passenger details are required.")
        people = [_passenger(p, i) for i, p in enumerate(passengers, 1)]
        offer = _own_offer(offer_id, "flight", user_id)
        if offer and len(people) != offer["summary"]["passengers"]:
            raise Invalid(f"This offer is for {offer['summary']['passengers']} passenger(s); "
                          f"got details for {len(people)}.")
    except Invalid as e:
        return _fail(e)
    lead = f"{people[0]['given_name']} {people[0]['family_name']}"
    return _book("flight", offer_id, email, lead,
                 {"passengers": people, "email": email, "phone": phone}, user_id)


def _lead_booking(kind: str, offer_id: str, given_name: str, family_name: str,
                  email: str, phone: str, user_id: int | None, extra: dict | None = None) -> dict:
    try:
        email, phone = _contact(email, phone)
        given, family = _name(given_name, "given_name"), _name(family_name, "family_name")
    except Invalid as e:
        return _fail(e)
    details = {"given_name": given, "family_name": family, "email": email, "phone": phone, **(extra or {})}
    return _book(kind, offer_id, email, f"{given} {family}", details, user_id)


def book_hotel(offer_id: str, guest_given_name: str, guest_family_name: str,
               email: str, phone: str, user_id: int | None = None) -> dict:
    return _lead_booking("hotel", offer_id, guest_given_name, guest_family_name, email, phone, user_id)


def book_car(offer_id: str, driver_given_name: str, driver_family_name: str,
             email: str, phone: str, user_id: int | None = None) -> dict:
    return _lead_booking("car", offer_id, driver_given_name, driver_family_name, email, phone, user_id)


def book_train(offer_id: str, passengers: list[dict], email: str, phone: str,
               user_id: int | None = None) -> dict:
    try:
        if not passengers:
            raise Invalid("Passenger names are required.")
        names = [{"given_name": _name(p.get("given_name"), f"Passenger {i} given name"),
                  "family_name": _name(p.get("family_name"), f"Passenger {i} family name")}
                 for i, p in enumerate(passengers, 1)]
        offer = _own_offer(offer_id, "train", user_id)
        if offer and len(names) != offer["summary"].get("passengers", len(names)):
            raise Invalid(f"This offer is for {offer['summary']['passengers']} passenger(s); "
                          f"got {len(names)} name(s).")
    except Invalid as e:
        return _fail(e)
    return _lead_booking("train", offer_id, names[0]["given_name"], names[0]["family_name"],
                         email, phone, user_id, {"passengers": names})


# ---------------------------------------------------------------- manage

def find_trips(email: str) -> dict:
    return {"ok": True, "trips": store.find_bookings((email or "").strip().lower())}


def all_trips() -> dict:
    return {"ok": True, "trips": store.all_bookings()}


def cancel_trip(booking_id: str, email: str, any_owner: bool = False) -> dict:
    # Ownership check: the trip must belong to the signed-in user's email. Admins may cancel any.
    booking = store.get_booking((booking_id or "").strip().upper(),
                                None if any_owner else (email or "").strip().lower())
    if not booking:
        return {"ok": False, "error": "No active trip with that booking id on your account."}
    provider = get_provider(booking["type"])
    if provider.name != booking["provider"]:
        return {"ok": False, "error": "This trip was booked with a different supplier configuration; "
                                      "cancel it with the supplier directly."}
    try:
        result = provider.cancel(booking)
    except ProviderError as e:
        return _fail(e)
    except Exception:
        log.exception("cancel failed for %s", booking["booking_id"])
        return {"ok": False, "error": "The cancellation could not be completed. The trip is still booked."}
    store.mark_cancelled(booking["booking_id"])
    return {"ok": True, "booking_id": booking["booking_id"], "status": "cancelled",
            "refund_amount": result.get("refund_amount"), "refund_currency": result.get("currency")}
