"""Persistence for travel: offers from searches, and confirmed trips (SQLite or PostgreSQL).

Offers are stored server-side so the model only ever passes an offer_id.
Prices and supplier ids never round-trip through the LLM, so it can't alter them.
Uses the same database as db.py (see database.py); the schema is in database.MIGRATIONS.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import database
from db import _connect

OFFER_TTL = timedelta(minutes=30)


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def init() -> None:
    database.migrate()
    purge_expired_offers()


def purge_expired_offers() -> int:
    """Delete unbooked offers past their expiry. Also run periodically (k8s CronJob)."""
    with _connect() as conn:
        return conn.execute("DELETE FROM travel_offers WHERE claimed = 0 AND expires_at < ?",
                            (_now().isoformat(),)).rowcount


def save_offers(kind: str, provider: str, offers: list[dict], user_id: int | None = None) -> list[dict]:
    """Persist offers for the user who searched; return the public view (offer_id + summary + price)."""
    default_expiry = _now() + OFFER_TTL
    public = []
    with _connect() as conn:
        for o in offers:
            offer_id = f"{kind[0].upper()}O-{uuid.uuid4().hex[:8].upper()}"
            expires = min(filter(None, [_parse_ts(o.get("expires_at")), default_expiry]))
            conn.execute(
                """INSERT INTO travel_offers (id, kind, provider, summary, price, currency, raw,
                                             expires_at, user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (offer_id, kind, provider, json.dumps(o["summary"]), o["price"], o["currency"],
                 json.dumps(o["raw"]), expires.isoformat(), user_id),
            )
            public.append({"offer_id": offer_id, **o["summary"], "total_price": o["price"],
                           "currency": o["currency"]})
    return public


def get_offer(offer_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM travel_offers WHERE id = ?",
                           (offer_id.strip().upper(),)).fetchone()
    if not row:
        return None
    return {"id": row["id"], "kind": row["kind"], "provider": row["provider"],
            "summary": json.loads(row["summary"]), "price": row["price"], "currency": row["currency"],
            "raw": json.loads(row["raw"]), "claimed": bool(row["claimed"]),
            "expired": _parse_ts(row["expires_at"]) < _now(), "user_id": row["user_id"]}


def claim_offer(offer_id: str) -> bool:
    """Atomically reserve an offer so two requests can't book it twice."""
    with _connect() as conn:
        cur = conn.execute("UPDATE travel_offers SET claimed = 1 WHERE id = ? AND claimed = 0",
                           (offer_id,))
    return cur.rowcount == 1


def release_offer(offer_id: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE travel_offers SET claimed = 0 WHERE id = ?", (offer_id,))


def save_booking(offer: dict, result: dict, email: str, lead_traveller: str) -> str:
    booking_id = "TR-" + uuid.uuid4().hex[:8].upper()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO travel_bookings (id, kind, provider, offer_id, reference, customer_email,
                   lead_traveller, summary, price, currency, raw, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?)""",
            (booking_id, offer["kind"], offer["provider"], offer["id"], result["reference"],
             email, lead_traveller, json.dumps(offer["summary"]), result["price"],
             result["currency"], json.dumps(result.get("raw", {})), _now().isoformat()),
        )
    return booking_id


def _public_booking(row) -> dict:
    return {"booking_id": row["id"], "type": row["kind"], "supplier_reference": row["reference"],
            "lead_traveller": row["lead_traveller"], "details": json.loads(row["summary"]),
            "total_price": row["price"], "currency": row["currency"], "status": row["status"]}


def find_bookings(email: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM travel_bookings WHERE customer_email = ? AND status = 'confirmed'
               ORDER BY created_at""", (email,)).fetchall()
    return [_public_booking(r) for r in rows]


def all_bookings() -> list[dict]:
    """Every confirmed trip (admin view)."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM travel_bookings WHERE status = 'confirmed' "
                            "ORDER BY created_at DESC LIMIT 200").fetchall()
    return [{**_public_booking(r), "customer_email": r["customer_email"]} for r in rows]


def get_booking(booking_id: str, email: str | None) -> dict | None:
    """Fetch an active trip owned by `email`, or by anyone when email is None (admin)."""
    sql = "SELECT * FROM travel_bookings WHERE id = ? AND status = 'confirmed'"
    args: tuple = (booking_id,)
    if email is not None:
        sql += " AND customer_email = ?"
        args += (email,)
    with _connect() as conn:
        row = conn.execute(sql, args).fetchone()
    if not row:
        return None
    return {**_public_booking(row), "provider": row["provider"], "reference": row["reference"],
            "raw": json.loads(row["raw"])}


def mark_cancelled(booking_id: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE travel_bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
