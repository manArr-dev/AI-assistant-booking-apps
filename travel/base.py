"""The interface every travel API integration implements.

A provider only talks to its API and maps responses to plain dicts.
Validation, ownership checks and persistence live in service.py and store.py,
so adding a new supplier means writing one small class.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

KINDS = ("flight", "hotel", "car", "train")


class ProviderError(Exception):
    """A supplier failure whose message is safe to show the customer."""


class TravelProvider(ABC):
    name: str = "base"
    kind: str = ""  # one of KINDS

    @abstractmethod
    def search(self, query: dict) -> list[dict]:
        """Search inventory. `query` has already been validated by service.py.

        Return a list of offers, each:
            {"summary": {...shown to the customer...},
             "price": float, "currency": "EUR",
             "expires_at": ISO-8601 str or None,
             "raw": {...whatever book() needs later; never shown...}}
        """

    @abstractmethod
    def book(self, offer: dict, details: dict) -> dict:
        """Book one offer returned by search(). `details` holds traveller/contact data.

        Return {"reference": str, "price": float, "currency": str, "raw": {...}}.
        Raise ProviderError if the supplier refuses (sold out, price changed...).
        """

    @abstractmethod
    def cancel(self, booking: dict) -> dict:
        """Cancel a booking made by book(). `booking` has "reference" and "raw".

        Return {"refund_amount": float | None, "currency": str | None}.
        """
