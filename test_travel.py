"""
Test the travel MCP server end to end with the mock providers (no API keys, no LLM).

Searches and books a flight, hotel, car and train as a signed-in user, then checks
double-booking, validation, and authorization: anonymous calls are refused, another
user can't book your offers or see/cancel your trips, an admin can cancel.
Uses a throwaway database, so your real bookings.db is untouched.

    python test_travel.py
"""
import asyncio
from datetime import date, timedelta

from test_helpers import call, make_user, mcp_session

SERVER = "travel_server.py"
PHONE = "+447700900123"


def day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


async def main():
    ada = make_user("ada@example.com", "Ada Lovelace")
    mallory = make_user("mallory@example.com", "Mallory Other")
    admin = make_user("admin@example.com", "Site Admin", role="admin")

    async with mcp_session(SERVER, None) as anon:
        print("Tools:", [t.name for t in (await anon.list_tools()).tools])
        denied = await call(anon, "search_flights", origin="LHR", destination="LIS", departure_date=day(20))
        assert not denied["ok"], "anonymous calls must be refused"

    async with mcp_session(SERVER, ada) as s:
        # --- flight
        flights = await call(s, "search_flights", origin="lhr", destination="LIS",
                             departure_date=day(20), return_date=day(27), adults=2)
        assert flights["ok"] and flights["offers"], flights
        fo = flights["offers"][0]["offer_id"]
        passengers = [
            {"title": "ms", "given_name": "Ada", "family_name": "Lovelace",
             "born_on": "1990-12-10", "gender": "f"},
            {"title": "mr", "given_name": "Alan", "family_name": "Turing",
             "born_on": "1988-06-23", "gender": "m"},
        ]
        short = await call(s, "book_flight", offer_id=fo, passengers=passengers[:1], phone=PHONE)
        assert not short["ok"], "wrong passenger count should be rejected"
        flight = await call(s, "book_flight", offer_id=fo, passengers=passengers, phone=PHONE)
        assert flight["ok"], flight
        again = await call(s, "book_flight", offer_id=fo, passengers=passengers, phone=PHONE)
        assert not again["ok"], "the same offer must not be booked twice"

        # --- hotel
        hotels = await call(s, "search_hotels", city="Lisbon", check_in=day(20), check_out=day(27), guests=2)
        assert hotels["ok"] and hotels["offers"], hotels
        hotel = await call(s, "book_hotel", offer_id=hotels["offers"][0]["offer_id"],
                           guest_given_name="Ada", guest_family_name="Lovelace", phone=PHONE)
        assert hotel["ok"], hotel

        # --- car
        cars = await call(s, "search_cars", pickup_location="LIS", pickup_date=day(20),
                          pickup_time="10:00", dropoff_date=day(27), dropoff_time="09:00", driver_age=34)
        assert cars["ok"] and cars["offers"], cars
        car = await call(s, "book_car", offer_id=cars["offers"][0]["offer_id"],
                         driver_given_name="Alan", driver_family_name="Turing", phone=PHONE)
        assert car["ok"], car
        spare_car_offer = cars["offers"][1]["offer_id"]

        # --- train
        trains = await call(s, "search_trains", origin="Lisbon", destination="Porto",
                            departure_date=day(23), earliest_time="08:00", adults=2)
        assert trains["ok"] and trains["offers"], trains
        train = await call(s, "book_train", offer_id=trains["offers"][0]["offer_id"],
                           passengers=[{"given_name": "Ada", "family_name": "Lovelace"},
                                       {"given_name": "Alan", "family_name": "Turing"}], phone=PHONE)
        assert train["ok"], train

        # --- validation is enforced server-side
        for name, args in [
            ("search_flights", {"origin": "London", "destination": "LIS", "departure_date": day(5)}),
            ("search_flights", {"origin": "LHR", "destination": "LIS", "departure_date": day(-1)}),
            ("search_hotels", {"city": "Lisbon", "check_in": day(10), "check_out": day(10)}),
            ("search_cars", {"pickup_location": "LIS", "pickup_date": day(9), "pickup_time": "10:00",
                             "dropoff_date": day(8), "dropoff_time": "10:00"}),
            ("book_car", {"offer_id": spare_car_offer, "driver_given_name": "A",
                          "driver_family_name": "B", "phone": "0770090"}),
            ("book_hotel", {"offer_id": spare_car_offer, "guest_given_name": "A",
                            "guest_family_name": "B", "phone": PHONE}),
        ]:
            bad = await call(s, name, **args)
            assert not bad["ok"], f"{name} should have rejected {args}"

        trips = await call(s, "find_my_trips")
        assert {t["type"] for t in trips["trips"]} == {"flight", "hotel", "car", "train"}, trips

    # --- authorization: another customer
    async with mcp_session(SERVER, mallory) as s:
        hijack = await call(s, "book_car", offer_id=spare_car_offer, driver_given_name="Mal",
                            driver_family_name="Lory", phone=PHONE)
        assert not hijack["ok"], "offers belong to the user who searched"
        theirs = await call(s, "find_my_trips")
        assert theirs["trips"] == [], "must not see other users' trips"
        steal = await call(s, "cancel_trip", booking_id=car["booking_id"])
        assert not steal["ok"], "must not cancel other users' trips"
        forbidden = await call(s, "admin_list_trips")
        assert not forbidden["ok"], "customers can't use admin tools"

    async with mcp_session(SERVER, ada) as s:
        ok = await call(s, "cancel_trip", booking_id=car["booking_id"])
        assert ok["ok"], ok

    async with mcp_session(SERVER, admin) as s:
        everyone = await call(s, "admin_list_trips")
        assert len(everyone["trips"]) == 3, everyone
        ok = await call(s, "cancel_trip", booking_id=train["booking_id"])
        assert ok["ok"], "admins may cancel any trip"

    print("\nAll travel checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
