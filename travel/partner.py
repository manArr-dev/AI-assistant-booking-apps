"""Generic REST adapter for a car-hire or rail partner API.

Car rental and rail booking APIs (e.g. Booking.com Demand API for cars,
Trainline / Rail Europe / SilverRail for trains) are only available under a
partner contract, and each has its own format. Put a thin service of your own
in front of the one you sign with, exposing this small contract, and set:

    CAR_PROVIDER=partner    CAR_API_URL=https://...    CAR_API_KEY=...
    TRAIN_PROVIDER=partner  TRAIN_API_URL=https://...  TRAIN_API_KEY=...

Contract (JSON, Authorization: Bearer <key>):

    POST /search                   body: {"kind": "car"|"train", ...search fields}
        -> {"offers": [{"id", "price", "currency", "expires_at"?, "summary": {...}}]}
    POST /bookings                 body: {"offer_id", ...traveller/contact fields}
        -> {"reference", "price", "currency"}
    POST /bookings/{reference}/cancel
        -> {"refund_amount"?, "currency"?}

Errors: any 4xx/5xx with {"error": "message safe to show the customer"}.
Alternatively, subclass TravelProvider and call the partner's API directly.
"""
from __future__ import annotations

import os

import httpx

from .base import ProviderError, TravelProvider


class PartnerAPIProvider(TravelProvider):
    name = "partner"

    def __init__(self, kind: str, base_url: str | None = None, api_key: str | None = None,
                 transport: httpx.BaseTransport | None = None):
        self.kind = kind
        base_url = base_url or os.environ.get(f"{kind.upper()}_API_URL", "")
        api_key = api_key or os.environ.get(f"{kind.upper()}_API_KEY", "")
        if not base_url:
            raise RuntimeError(f"{kind.upper()}_API_URL is not set (see travel/partner.py).")
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0, transport=transport,
                                 headers={"Authorization": f"Bearer {api_key}",
                                          "Accept": "application/json"})

    def _post(self, path: str, body: dict | None = None) -> dict:
        try:
            r = self.http.post(path, json=body or {})
        except httpx.HTTPError as e:
            raise ProviderError(f"The {self.kind} supplier could not be reached. Try again shortly.") from e
        if r.status_code >= 400:
            try:
                message = r.json().get("error")
            except ValueError:
                message = None
            raise ProviderError(message or f"{self.kind.title()} supplier error {r.status_code}.")
        return r.json() if r.content else {}

    def search(self, q: dict) -> list[dict]:
        data = self._post("/search", {"kind": self.kind, **q})
        return [{"summary": o.get("summary", {}), "price": float(o["price"]), "currency": o["currency"],
                 "expires_at": o.get("expires_at"), "raw": {"id": o["id"]}}
                for o in data.get("offers", [])]

    def book(self, offer: dict, details: dict) -> dict:
        data = self._post("/bookings", {"offer_id": offer["raw"]["id"], **details})
        return {"reference": data["reference"], "price": float(data.get("price", offer["price"])),
                "currency": data.get("currency", offer["currency"]), "raw": {}}

    def cancel(self, booking: dict) -> dict:
        data = self._post(f"/bookings/{booking['reference']}/cancel")
        return {"refund_amount": data.get("refund_amount"), "currency": data.get("currency")}
