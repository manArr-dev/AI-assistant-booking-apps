"""
REST API for the React frontend (frontend/). Uses the same auth and travel code
as the MCP servers, so the rules are identical in the chat and the web app.

    uvicorn api_server:app --port 8080            # API (+ the built frontend, if present)
    cd frontend && npm run dev                    # dev UI on http://localhost:5173, proxies /api

Security:
- Session token lives in an httpOnly, SameSite=Strict cookie (JavaScript can't read it).
  Set COOKIE_SECURE=true when serving over HTTPS.
- Every state-changing /api request must send  X-Requested-With: booking-web  (CSRF defence
  on top of SameSite; a cross-site form can't set custom headers).
- Login is two-step: password -> short-lived challenge -> MFA code -> session cookie.
- /api/auth/* POSTs are rate-limited per IP; accounts also lock after 5 failures (auth.py).
- Roles: every endpoint requires a session; /api/admin/* requires role=admin.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request, Response  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

import auth  # noqa: E402
import cache  # noqa: E402
import database  # noqa: E402
import db  # noqa: E402
from auth import AuthError  # noqa: E402
from travel import service, store  # noqa: E402

COOKIE = "bs_session"
CSRF_HEADER, CSRF_VALUE = "x-requested-with", "booking-web"
COOKIE_SECURE = auth._env_flag("COOKIE_SECURE", False)
AUTH_RATE_LIMIT = int(os.environ.get("AUTH_RATE_LIMIT", "30"))  # requests per IP per window
AUTH_RATE_WINDOW = 300  # seconds
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
HHMM = r"^(|([01]\d|2[0-3]):[0-5]\d)$"   # empty or 24-hour HH:MM
FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST") or Path(__file__).with_name("frontend") / "dist")

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

# Migrations normally run once per deploy (k8s init container / `python migrate.py`);
# running them here too is idempotent and keeps local development to one command.
db.init_db()
auth.init()
store.init()

app = FastAPI(title="Booking assistant API", docs_url="/api/docs", redoc_url=None,
              openapi_url="/api/openapi.json")


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Liveness: the process is up. Never touches dependencies, so a DB outage doesn't restart pods."""
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
def readyz():
    """Readiness: the database answers. Redis is optional (degraded, still ready)."""
    db_ok = database.ping()
    redis_ok = cache.ping()
    body = {"database": "ok" if db_ok else "down",
            "redis": {None: "not configured", True: "ok", False: "degraded"}[redis_ok],
            "engine": "postgresql" if database.is_postgres() else "sqlite"}
    return JSONResponse(body, status_code=200 if db_ok else 503)


@app.middleware("http")
async def security(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and request.method not in SAFE_METHODS:
        if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            return JSONResponse({"detail": "Missing CSRF header."}, status_code=403)
        if path.startswith("/api/auth/"):
            # Counted in Redis, so the limit holds across every API replica. Behind a proxy,
            # run uvicorn with --proxy-headers so request.client is the real client IP.
            ip = request.client.host if request.client else "unknown"
            if cache.hit_rate_limit(f"auth:{ip}", AUTH_RATE_LIMIT, AUTH_RATE_WINDOW):
                return JSONResponse({"detail": "Too many attempts. Wait a few minutes."}, status_code=429)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; object-src 'none'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(AuthError)
async def auth_error(_: Request, e: AuthError):
    return JSONResponse({"detail": str(e)}, status_code=401)


# ---------------------------------------------------------------- session helpers

def _token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return request.cookies.get(COOKIE) or (header[7:] if header.lower().startswith("bearer ") else None)


def current_user(request: Request) -> dict:
    user = auth.user_for_token(_token(request))
    if not user:
        raise HTTPException(401, "Please sign in.")
    return user


def admin_user(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Admins only.")
    return user


def _set_session(response: Response, token: str) -> None:
    response.set_cookie(COOKIE, token, httponly=True, secure=COOKIE_SECURE, samesite="strict",
                        max_age=int(auth.session_lifetime().total_seconds()), path="/")


def _public(user: dict) -> dict:
    return {k: user[k] for k in ("id", "email", "full_name", "role", "mfa_enabled")}


def _ok(result: dict) -> dict:
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Request failed."))
    return result


# ---------------------------------------------------------------- auth

class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=auth.MAX_PASSWORD_LEN)


class Registration(Credentials):
    full_name: str = Field(max_length=100)


class Challenge(BaseModel):
    challenge: str = Field(max_length=100)


class ChallengeCode(Challenge):
    code: str = Field(max_length=20)


def _after_password(user: dict, response: Response) -> dict:
    if auth.mfa_required(user):
        return {"status": "mfa_verify" if user["mfa_enabled"] else "mfa_setup",
                "challenge": auth.create_login_challenge(user["id"])}
    _set_session(response, auth.issue_session(user["id"], mfa_verified=False))
    return {"status": "signed_in", "user": _public(user)}


def _challenge_user(challenge: str) -> dict:
    user = auth.challenge_user(challenge)
    if not user:
        raise HTTPException(401, "Your sign-in attempt expired. Please sign in again.")
    return user


@app.post("/api/auth/register")
def register(body: Registration, response: Response):
    user = auth.register(body.email, body.password, body.full_name)
    return _after_password(user, response)


@app.post("/api/auth/login")
def login(body: Credentials, response: Response):
    return _after_password(auth.check_password(body.email, body.password), response)


@app.post("/api/auth/mfa/setup")
def mfa_setup(body: Challenge):
    user = _challenge_user(body.challenge)
    if user["mfa_enabled"]:  # a password alone must never be enough to re-pair MFA
        raise HTTPException(409, "Two-factor authentication is already set up.")
    return auth.begin_mfa_enrollment(user["id"])


@app.post("/api/auth/mfa/confirm")
def mfa_confirm(body: ChallengeCode, response: Response):
    user = _challenge_user(body.challenge)
    if user["mfa_enabled"]:
        raise HTTPException(409, "Two-factor authentication is already set up.")
    codes = auth.confirm_mfa_enrollment(user["id"], body.code)
    auth.finish_login_challenge(body.challenge)
    _set_session(response, auth.issue_session(user["id"], mfa_verified=True))
    return {"status": "signed_in", "user": {**_public(user), "mfa_enabled": True}, "recovery_codes": codes}


@app.post("/api/auth/mfa/verify")
def mfa_verify(body: ChallengeCode, response: Response):
    user = _challenge_user(body.challenge)
    auth.verify_mfa(user["id"], body.code)
    auth.finish_login_challenge(body.challenge)
    _set_session(response, auth.issue_session(user["id"], mfa_verified=True))
    return {"status": "signed_in", "user": _public(user),
            "recovery_codes_left": auth.remaining_recovery_codes(user["id"])}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    if token := _token(request):
        auth.revoke_session(token)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request):
    """The signed-in user, or {"user": null}. Never 401, so a signed-out page load is quiet."""
    user = auth.user_for_token(_token(request))
    return {"user": _public(user) if user else None}


# ---------------------------------------------------------------- search

def _within(iso: str, after: str, before: str) -> bool:
    hhmm = (iso or "")[11:16]
    return (not after or hhmm >= after) and (not before or hhmm <= before)


class FlightSearch(BaseModel):
    origin: str = Field(max_length=3)
    destination: str = Field(max_length=3)
    departure_date: str = Field(max_length=10)
    return_date: str = Field("", max_length=10)
    adults: int = Field(1, ge=1, le=9)
    cabin_class: str = Field("economy", max_length=20)
    depart_after: str = Field("", pattern=HHMM)
    depart_before: str = Field("", pattern=HHMM)


class HotelSearch(BaseModel):
    city: str = Field(max_length=100)
    check_in: str = Field(max_length=10)
    check_out: str = Field(max_length=10)
    guests: int = Field(1, ge=1, le=9)
    rooms: int = Field(1, ge=1, le=5)


class CarSearch(BaseModel):
    pickup_location: str = Field(max_length=100)
    pickup_date: str = Field(max_length=10)
    pickup_time: str = Field(pattern=HHMM)
    dropoff_date: str = Field(max_length=10)
    dropoff_time: str = Field(pattern=HHMM)
    dropoff_location: str = Field("", max_length=100)
    driver_age: int = Field(30, ge=18, le=99)


class TrainSearch(BaseModel):
    origin: str = Field(max_length=100)
    destination: str = Field(max_length=100)
    departure_date: str = Field(max_length=10)
    adults: int = Field(1, ge=1, le=9)
    travel_class: str = Field("standard", max_length=20)
    depart_after: str = Field("", pattern=HHMM)
    depart_before: str = Field("", pattern=HHMM)


@app.post("/api/search/flights")
def search_flights(q: FlightSearch, user: dict = Depends(current_user)):
    result = _ok(service.search_flights(q.origin, q.destination, q.departure_date, q.return_date,
                                        q.adults, q.cabin_class, user_id=user["id"]))
    result["offers"] = [o for o in result["offers"]
                        if _within(o["outbound"]["depart"], q.depart_after, q.depart_before)]
    return result


@app.post("/api/search/hotels")
def search_hotels(q: HotelSearch, user: dict = Depends(current_user)):
    return _ok(service.search_hotels(q.city, q.check_in, q.check_out, q.guests, q.rooms, user_id=user["id"]))


@app.post("/api/search/cars")
def search_cars(q: CarSearch, user: dict = Depends(current_user)):
    return _ok(service.search_cars(q.pickup_location, q.pickup_date, q.pickup_time, q.dropoff_date,
                                   q.dropoff_time, q.dropoff_location, q.driver_age, user_id=user["id"]))


@app.post("/api/search/trains")
def search_trains(q: TrainSearch, user: dict = Depends(current_user)):
    result = _ok(service.search_trains(q.origin, q.destination, q.departure_date, q.depart_after or "00:00",
                                       q.adults, q.travel_class, user_id=user["id"]))
    result["offers"] = [o for o in result["offers"] if _within(o["depart"], q.depart_after, q.depart_before)]
    return result


# ---------------------------------------------------------------- book

class FlightPassenger(BaseModel):
    title: str = Field(max_length=5)
    given_name: str = Field(max_length=60)
    family_name: str = Field(max_length=60)
    born_on: str = Field(max_length=10)
    gender: str = Field(max_length=1)


class TravellerName(BaseModel):
    given_name: str = Field(max_length=60)
    family_name: str = Field(max_length=60)


class FlightBooking(BaseModel):
    offer_id: str = Field(max_length=20)
    passengers: list[FlightPassenger] = Field(max_length=9)
    phone: str = Field(max_length=20)


class LeadBooking(BaseModel):
    offer_id: str = Field(max_length=20)
    given_name: str = Field(max_length=60)
    family_name: str = Field(max_length=60)
    phone: str = Field(max_length=20)


class TrainBooking(BaseModel):
    offer_id: str = Field(max_length=20)
    passengers: list[TravellerName] = Field(max_length=9)
    phone: str = Field(max_length=20)


@app.post("/api/book/flight")
def book_flight(b: FlightBooking, user: dict = Depends(current_user)):
    return _ok(service.book_flight(b.offer_id, [p.model_dump() for p in b.passengers], user["email"],
                                   b.phone, user_id=user["id"]))


@app.post("/api/book/hotel")
def book_hotel(b: LeadBooking, user: dict = Depends(current_user)):
    return _ok(service.book_hotel(b.offer_id, b.given_name, b.family_name, user["email"], b.phone,
                                  user_id=user["id"]))


@app.post("/api/book/car")
def book_car(b: LeadBooking, user: dict = Depends(current_user)):
    return _ok(service.book_car(b.offer_id, b.given_name, b.family_name, user["email"], b.phone,
                                user_id=user["id"]))


@app.post("/api/book/train")
def book_train(b: TrainBooking, user: dict = Depends(current_user)):
    return _ok(service.book_train(b.offer_id, [p.model_dump() for p in b.passengers], user["email"],
                                  b.phone, user_id=user["id"]))


# ---------------------------------------------------------------- trips

@app.get("/api/trips")
def my_trips(user: dict = Depends(current_user)):
    return service.find_trips(user["email"])


@app.post("/api/trips/{booking_id}/cancel")
def cancel_trip(booking_id: str, user: dict = Depends(current_user)):
    return _ok(service.cancel_trip(booking_id[:20], user["email"], any_owner=user["role"] == "admin"))


@app.get("/api/admin/trips")
def admin_trips(_: dict = Depends(admin_user)):
    return service.all_trips()


@app.get("/api/admin/users")
def admin_users(_: dict = Depends(admin_user)):
    return {"ok": True, "users": auth.list_users()}


# ---------------------------------------------------------------- built frontend (production)

if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        file = (FRONTEND_DIST / path).resolve()
        if path and file.is_file() and FRONTEND_DIST.resolve() in file.parents:
            return FileResponse(file)
        return FileResponse(FRONTEND_DIST / "index.html")
