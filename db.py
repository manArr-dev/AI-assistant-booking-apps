"""
Step 2 — Data layer.

All booking rules live here, in plain Python, NOT in the LLM prompt.
The model can ask for anything; this module decides what is actually allowed.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta

import database
from database import Connection

OPEN_HOUR = 9          # business opens 09:00
CLOSE_HOUR = 17        # business closes 17:00
SLOT_MINUTES = 30      # bookings start on a 30-minute grid
MAX_DAYS_AHEAD = 60    # how far ahead customers may book

SEED_SERVICES = [
    # id, name, duration (minutes), price
    ("haircut", "Haircut", 30, 35.0),
    ("consultation", "Consultation", 60, 80.0),
    ("massage", "Massage (60 min)", 60, 95.0),
    ("coloring", "Hair coloring", 90, 120.0),
]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _connect() -> Connection:
    """A connection to SQLite or PostgreSQL, depending on DATABASE_URL (see database.py)."""
    return database.connect()


def init_db() -> None:
    database.migrate()
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO services (id, name, duration_min, price) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
            SEED_SERVICES,
        )


# ---------- helpers ----------

def _get_service(conn: Connection, service_id: str) -> dict | None:
    return conn.execute(
        "SELECT * FROM services WHERE id = ?", (service_id.strip().lower(),)
    ).fetchone()


def _parse_day(day: str) -> date:
    return date.fromisoformat(day.strip())


def _validate_day(d: date) -> str | None:
    today = date.today()
    if d < today:
        return "That date is in the past."
    if d > today + timedelta(days=MAX_DAYS_AHEAD):
        return f"Bookings can only be made up to {MAX_DAYS_AHEAD} days ahead."
    if d.weekday() >= 5:
        return "We are closed on weekends (open Monday to Friday)."
    return None


def _overlaps(conn: Connection, start: datetime, end: datetime) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM bookings
        WHERE status = 'confirmed' AND start_ts < ? AND end_ts > ?
        LIMIT 1
        """,
        (end.isoformat(), start.isoformat()),
    ).fetchone()
    return row is not None


def _booking_dict(row) -> dict:
    return {
        "booking_id": row["id"],
        "service_id": row["service_id"],
        "customer_name": row["customer_name"],
        "start": row["start_ts"],
        "end": row["end_ts"],
        "status": row["status"],
    }


# ---------- public operations (used by the MCP server) ----------

def list_services() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM services ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def available_slots(service_id: str, day: str) -> dict:
    try:
        d = _parse_day(day)
    except ValueError:
        return {"ok": False, "error": "Date must be in YYYY-MM-DD format."}
    if err := _validate_day(d):
        return {"ok": False, "error": err}

    with _connect() as conn:
        svc = _get_service(conn, service_id)
        if not svc:
            return {"ok": False, "error": f"Unknown service '{service_id}'. Call list_services first."}

        duration = timedelta(minutes=svc["duration_min"])
        cursor = datetime.combine(d, datetime.min.time()).replace(hour=OPEN_HOUR)
        close = cursor.replace(hour=CLOSE_HOUR)
        now = datetime.now()
        slots = []
        while cursor + duration <= close:
            if cursor > now and not _overlaps(conn, cursor, cursor + duration):
                slots.append(cursor.strftime("%H:%M"))
            cursor += timedelta(minutes=SLOT_MINUTES)

    return {"ok": True, "service": svc["name"], "date": d.isoformat(),
            "duration_min": svc["duration_min"], "available_times": slots}


def create_booking(service_id: str, day: str, start_time: str,
                   customer_name: str, customer_email: str) -> dict:
    if not customer_name.strip():
        return {"ok": False, "error": "Customer name is required."}
    if not EMAIL_RE.match(customer_email.strip()):
        return {"ok": False, "error": "A valid customer email is required."}
    try:
        d = _parse_day(day)
        t = datetime.strptime(start_time.strip(), "%H:%M").time()
    except ValueError:
        return {"ok": False, "error": "Use date YYYY-MM-DD and time HH:MM (24-hour)."}
    if err := _validate_day(d):
        return {"ok": False, "error": err}
    if t.minute % SLOT_MINUTES:
        return {"ok": False, "error": f"Start times must be on a {SLOT_MINUTES}-minute boundary."}

    conn = _connect()
    try:
        # A transaction holding the "bookings" lock (SQLite: write lock, Postgres: advisory
        # lock), so two concurrent requests, even on different pods, can't both pass the
        # overlap check and double-book the same slot.
        conn.begin(lock="bookings")
        svc = _get_service(conn, service_id)
        if not svc:
            conn.rollback()
            return {"ok": False, "error": f"Unknown service '{service_id}'."}

        start = datetime.combine(d, t)
        end = start + timedelta(minutes=svc["duration_min"])
        if start <= datetime.now():
            conn.rollback()
            return {"ok": False, "error": "That time has already passed."}
        if start.hour < OPEN_HOUR or end > start.replace(hour=CLOSE_HOUR, minute=0):
            conn.rollback()
            return {"ok": False, "error": f"Appointments must fit between {OPEN_HOUR}:00 and {CLOSE_HOUR}:00."}
        if _overlaps(conn, start, end):
            conn.rollback()
            return {"ok": False, "error": "That slot was just taken. Check availability again."}

        booking_id = "BK-" + uuid.uuid4().hex[:8].upper()
        conn.execute(
            """INSERT INTO bookings (id, service_id, customer_name, customer_email,
                                     start_ts, end_ts, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'confirmed', ?)""",
            (booking_id, svc["id"], customer_name.strip(), customer_email.strip().lower(),
             start.isoformat(), end.isoformat(), datetime.now().isoformat()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"ok": True, "booking_id": booking_id, "service": svc["name"],
            "start": start.isoformat(), "end": end.isoformat(), "price": svc["price"]}


def find_bookings(customer_email: str) -> dict:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM bookings
               WHERE customer_email = ? AND status = 'confirmed' AND start_ts >= ?
               ORDER BY start_ts""",
            (customer_email.strip().lower(), datetime.now().isoformat()),
        ).fetchall()
    return {"ok": True, "bookings": [_booking_dict(r) for r in rows]}


def cancel_booking(booking_id: str, customer_email: str, any_owner: bool = False) -> dict:
    # Ownership check: the booking must belong to the signed-in customer's email
    # (taken from their session, not typed in). Admins may cancel any booking.
    with _connect() as conn:
        if any_owner:
            cur = conn.execute(
                "UPDATE bookings SET status = 'cancelled' WHERE id = ? AND status = 'confirmed'",
                (booking_id.strip().upper(),))
        else:
            cur = conn.execute(
                """UPDATE bookings SET status = 'cancelled'
                   WHERE id = ? AND customer_email = ? AND status = 'confirmed'""",
                (booking_id.strip().upper(), customer_email.strip().lower()))
    if cur.rowcount == 0:
        return {"ok": False, "error": "No active booking with that ID on your account."}
    return {"ok": True, "booking_id": booking_id.strip().upper(), "status": "cancelled"}


def bookings_on(day: str) -> dict:
    """All confirmed bookings on a day (admin view)."""
    try:
        d = _parse_day(day)
    except ValueError:
        return {"ok": False, "error": "Date must be in YYYY-MM-DD format."}
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM bookings WHERE status = 'confirmed' AND substr(start_ts, 1, 10) = ?
               ORDER BY start_ts""", (d.isoformat(),)).fetchall()
    return {"ok": True, "date": d.isoformat(),
            "bookings": [{**_booking_dict(r), "customer_email": r["customer_email"]} for r in rows]}
