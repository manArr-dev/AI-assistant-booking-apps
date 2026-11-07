"""Choose which API serves each kind of booking, from environment variables.

    FLIGHT_PROVIDER = mock | duffel
    HOTEL_PROVIDER  = mock | duffel
    CAR_PROVIDER    = mock | partner
    TRAIN_PROVIDER  = mock | partner

Everything defaults to mock, so the project runs with no keys.
"""
from __future__ import annotations

import os
from functools import cache

from . import mock
from .base import KINDS, TravelProvider

SUPPORTED = {
    "flight": ("mock", "duffel"),
    "hotel": ("mock", "duffel"),
    "car": ("mock", "partner"),
    "train": ("mock", "partner"),
}


def provider_name(kind: str) -> str:
    name = os.environ.get(f"{kind.upper()}_PROVIDER", "mock").strip().lower() or "mock"
    if name not in SUPPORTED[kind]:
        raise RuntimeError(f"{kind.upper()}_PROVIDER={name!r} is not one of {SUPPORTED[kind]}.")
    return name


@cache
def get_provider(kind: str) -> TravelProvider:
    if kind not in KINDS:
        raise ValueError(f"Unknown booking kind {kind!r}")
    name = provider_name(kind)
    if name == "mock":
        return {"flight": mock.MockFlights, "hotel": mock.MockHotels,
                "car": mock.MockCars, "train": mock.MockTrains}[kind]()
    if name == "duffel":
        from .duffel import DuffelFlights, DuffelHotels
        return DuffelFlights() if kind == "flight" else DuffelHotels()
    from .partner import PartnerAPIProvider
    return PartnerAPIProvider(kind)
