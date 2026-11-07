"""
Test the REST API used by the React frontend (no browser, no network; throwaway database).

Covers: sign-up -> MFA setup -> session cookie, MFA sign-in with a recovery code, CSRF header,
search with a departure time range, booking each kind, ownership, admin-only endpoints, logout.

    python test_api.py
"""

import test_helpers  # noqa: F401, I001  (must come first: isolates env + database)

# isort: split
from datetime import date, timedelta

from fastapi.testclient import TestClient

import api_server
import auth

H = {"X-Requested-With": "booking-web"}
PW = test_helpers.PASSWORD
PHONE = "+447700900123"


def day(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


def client() -> TestClient:
    return TestClient(api_server.app)


def sign_up(c: TestClient, email: str, name: str) -> list[str]:
    r = c.post("/api/auth/register", json={"email": email, "password": PW, "full_name": name}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "mfa_setup", r.text
    challenge = r.json()["challenge"]
    assert c.get("/api/auth/me").json() == {"user": None}, "no session before MFA"
    setup = c.post("/api/auth/mfa/setup", json={"challenge": challenge}, headers=H).json()
    assert setup["uri"].startswith("otpauth://totp/")
    bad = c.post("/api/auth/mfa/confirm", json={"challenge": challenge, "code": "000000"}, headers=H)
    assert bad.status_code == 401
    r = c.post("/api/auth/mfa/confirm", json={"challenge": challenge, "code": auth.totp_now(setup["secret"])},
               headers=H)
    assert r.status_code == 200, r.text
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    return r.json()["recovery_codes"]


def main():
    alice = client()
    codes = sign_up(alice, "alice@example.com", "Alice Example")
    me = alice.get("/api/auth/me").json()["user"]
    assert me["email"] == "alice@example.com" and me["role"] == "customer" and me["mfa_enabled"]
    print("ok   sign-up + MFA setup + session cookie")

    # CSRF: state-changing request without the header is refused
    assert alice.post("/api/search/hotels", json={}).status_code == 403
    print("ok   CSRF header required")

    # Flights with a departure time range
    r = alice.post("/api/search/flights", headers=H, json={
        "origin": "LHR", "destination": "LIS", "departure_date": day(20), "return_date": day(27),
        "adults": 1, "depart_after": "12:00", "depart_before": "23:59"})
    assert r.status_code == 200, r.text
    flights = r.json()["offers"]
    assert all("12:00" <= o["outbound"]["depart"][11:16] <= "23:59" for o in flights)
    unfiltered = alice.post("/api/search/flights", headers=H, json={
        "origin": "LHR", "destination": "LIS", "departure_date": day(20), "return_date": day(27)}).json()["offers"]
    assert len(unfiltered) >= len(flights)
    bad_time = alice.post("/api/search/flights", headers=H, json={
        "origin": "LHR", "destination": "LIS", "departure_date": day(20), "depart_after": "25:00"})
    assert bad_time.status_code == 422
    print(f"ok   flight time range ({len(flights)}/{len(unfiltered)} offers in window)")

    offer = (flights or unfiltered)[0]
    r = alice.post("/api/book/flight", headers=H, json={"offer_id": offer["offer_id"], "phone": PHONE, "passengers": [
        {"title": "ms", "given_name": "Alice", "family_name": "Example", "born_on": "1990-01-02", "gender": "f"}]})
    assert r.status_code == 200, r.text
    flight = r.json()

    hotel_offer = alice.post("/api/search/hotels", headers=H, json={
        "city": "Lisbon", "check_in": day(20), "check_out": day(27), "guests": 1}).json()["offers"][0]
    assert alice.post("/api/book/hotel", headers=H, json={
        "offer_id": hotel_offer["offer_id"], "given_name": "Alice", "family_name": "Example",
        "phone": PHONE}).status_code == 200

    cars = alice.post("/api/search/cars", headers=H, json={
        "pickup_location": "Lisbon", "pickup_date": day(20), "pickup_time": "10:00",
        "dropoff_date": day(27), "dropoff_time": "09:30"}).json()["offers"]
    assert alice.post("/api/book/car", headers=H, json={
        "offer_id": cars[0]["offer_id"], "given_name": "Alice", "family_name": "Example",
        "phone": PHONE}).status_code == 200

    trains = alice.post("/api/search/trains", headers=H, json={
        "origin": "Lisbon", "destination": "Porto", "departure_date": day(22),
        "depart_after": "08:00", "depart_before": "20:00"}).json()["offers"]
    assert trains and all("08:00" <= t["depart"][11:16] <= "20:00" for t in trains)
    assert alice.post("/api/book/train", headers=H, json={
        "offer_id": trains[0]["offer_id"], "phone": PHONE,
        "passengers": [{"given_name": "Alice", "family_name": "Example"}]}).status_code == 200

    trips = alice.get("/api/trips").json()["trips"]
    assert {t["type"] for t in trips} == {"flight", "hotel", "car", "train"}
    print("ok   booked flight, hotel, car and train")

    # Customer can't use admin endpoints
    assert alice.get("/api/admin/trips").status_code == 403
    assert alice.get("/api/admin/users").status_code == 403
    print("ok   customer blocked from /api/admin")

    # Another customer: can't book Alice's offers or cancel her trips
    bob = client()
    sign_up(bob, "bob@example.com", "Bob Example")
    hijack = bob.post("/api/book/car", headers=H, json={
        "offer_id": cars[1]["offer_id"], "given_name": "Bob", "family_name": "X", "phone": PHONE})
    assert hijack.status_code == 400
    assert bob.get("/api/trips").json()["trips"] == []
    assert bob.post(f"/api/trips/{flight['booking_id']}/cancel", headers=H).status_code == 400
    print("ok   other customer can't see, book or cancel Alice's items")

    # Admin sees everyone and can cancel any trip
    admin = client()
    sign_up(admin, "admin@example.com", "Ada Admin")
    auth.set_role("admin@example.com", "admin")
    users = admin.get("/api/admin/users").json()["users"]
    assert {u["email"] for u in users} >= {"alice@example.com", "bob@example.com"}
    all_trips = admin.get("/api/admin/trips").json()["trips"]
    assert len(all_trips) == 4 and all(t["customer_email"] == "alice@example.com" for t in all_trips)
    assert admin.post(f"/api/trips/{flight['booking_id']}/cancel", headers=H).status_code == 200
    assert len(alice.get("/api/trips").json()["trips"]) == 3
    print("ok   admin lists users/trips and cancels any trip")

    # Sign out, then sign back in with password + recovery code
    assert alice.post("/api/auth/logout", headers=H).status_code == 200
    assert alice.get("/api/trips").status_code == 401
    wrong = alice.post("/api/auth/login", headers=H, json={"email": "alice@example.com", "password": "nope nope nope"})
    assert wrong.status_code == 401
    r = alice.post("/api/auth/login", headers=H, json={"email": "alice@example.com", "password": PW})
    assert r.json()["status"] == "mfa_verify"
    challenge = r.json()["challenge"]
    assert alice.post("/api/auth/mfa/setup", json={"challenge": challenge}, headers=H).status_code == 409, \
        "a password alone must not be able to re-pair MFA"
    r = alice.post("/api/auth/mfa/verify", headers=H, json={"challenge": challenge, "code": codes[0]})
    assert r.status_code == 200, r.text
    assert alice.get("/api/trips").status_code == 200
    replay = alice.post("/api/auth/mfa/verify", headers=H, json={"challenge": challenge, "code": codes[1]})
    assert replay.status_code == 401, "challenge is single-use"
    print("ok   logout, MFA sign-in with recovery code, single-use challenge")

    print("\nAll API checks passed.")


if __name__ == "__main__":
    main()
