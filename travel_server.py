"""
MCP server for travel: flights, hotels, car hire and trains.

Every booking is two steps: search_* returns offers with an offer_id, then
book_* books one offer_id. Which real API serves each kind is set in .env
(FLIGHT_PROVIDER, HOTEL_PROVIDER, CAR_PROVIDER, TRAIN_PROVIDER; default mock).

Every tool requires a signed-in user (see mcp_auth.py). The account email comes
from the session; offers can only be booked by the user whose search produced
them, and trips can only be seen/cancelled by their owner (or an admin).

Run modes:
    python travel_server.py           # stdio (the agent launches it, passing the session)
    python travel_server.py --http    # streamable HTTP on http://localhost:8001/mcp,
                                      # requires  Authorization: Bearer <session token>
"""
import logging
import sys

from dotenv import load_dotenv
from pydantic import BaseModel, Field

try:
    # MCP Python SDK 2.x renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:
    # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as Server

load_dotenv()
logging.basicConfig(stream=sys.stderr, level=logging.INFO)  # stdout is the MCP channel in stdio mode

import auth  # noqa: E402
import db  # noqa: E402
import mcp_auth  # noqa: E402
from travel import service, store  # noqa: E402

HTTP_PORT = 8001  # booking_server.py uses 8000, so both can run over HTTP side by side
mcp = Server("travel", **mcp_auth.server_options(HTTP_PORT))


class FlightPassenger(BaseModel):
    title: str = Field(description="mr, ms, mrs, miss or dr")
    given_name: str = Field(description="First name exactly as on the passport")
    family_name: str = Field(description="Surname exactly as on the passport")
    born_on: str = Field(description="Date of birth, YYYY-MM-DD")
    gender: str = Field(description="m or f, as on the passport")


class TravellerName(BaseModel):
    given_name: str
    family_name: str


# ---------------------------------------------------------------- flights

@mcp.tool()
def search_flights(origin: str, destination: str, departure_date: str, return_date: str = "",
                   adults: int = 1, cabin_class: str = "economy") -> dict:
    """Search flights. Returns offers, each with an offer_id, times and total price.

    Args:
        origin: 3-letter IATA airport or city code, e.g. "LHR" or "LON".
        destination: 3-letter IATA airport or city code, e.g. "JFK" or "NYC".
        departure_date: YYYY-MM-DD.
        return_date: YYYY-MM-DD for a return trip; leave empty for one-way.
        adults: Number of adult passengers (1-9).
        cabin_class: economy, premium_economy, business or first.
    """
    user, err = mcp_auth.require()
    if err:
        return err
    return service.search_flights(origin, destination, departure_date, return_date, adults,
                                  cabin_class, user_id=user["id"])


@mcp.tool()
def book_flight(offer_id: str, passengers: list[FlightPassenger], phone: str) -> dict:
    """Book a flight offer from search_flights. Needs one passenger entry per traveller on the offer.
    Confirmation goes to the signed-in account's email.

    Args:
        offer_id: An offer_id from search_flights, e.g. "FO-1A2B3C4D".
        passengers: Each traveller's title, names, date of birth and gender as on their passport.
        phone: Contact phone in international format, e.g. "+447700900123".
    """
    user, err = mcp_auth.require()
    if err:
        return err
    return service.book_flight(offer_id, [p.model_dump() for p in passengers], user["email"], phone,
                               user_id=user["id"])


# ---------------------------------------------------------------- hotels

@mcp.tool()
def search_hotels(city: str, check_in: str, check_out: str, guests: int = 1, rooms: int = 1) -> dict:
    """Search hotels in a city. Returns offers with an offer_id and total price for the stay.

    Args:
        city: City or area name, e.g. "Lisbon" (or "latitude,longitude").
        check_in: YYYY-MM-DD.
        check_out: YYYY-MM-DD, after check_in.
        guests: Number of adult guests.
        rooms: Number of rooms (1-5).
    """
    user, err = mcp_auth.require()
    if err:
        return err
    return service.search_hotels(city, check_in, check_out, guests, rooms, user_id=user["id"])


@mcp.tool()
def book_hotel(offer_id: str, guest_given_name: str, guest_family_name: str, phone: str) -> dict:
    """Book a hotel offer from search_hotels for the lead guest.

    Args:
        offer_id: An offer_id from search_hotels, e.g. "HO-1A2B3C4D".
        guest_given_name: Lead guest's first name.
        guest_family_name: Lead guest's surname.
        phone: Contact phone in international format, e.g. "+447700900123".
    """
    user, err = mcp_auth.require()
    if err:
        return err
    return service.book_hotel(offer_id, guest_given_name, guest_family_name, user["email"], phone,
                              user_id=user["id"])


# ---------------------------------------------------------------- cars

@mcp.tool()
def search_cars(pickup_location: str, pickup_date: str, pickup_time: str, dropoff_date: str,
                dropoff_time: str, dropoff_location: str = "", driver_age: int = 30) -> dict:
    """Search rental cars. Returns offers with an offer_id and total rental price.

    Args:
        pickup_location: Airport code or city, e.g. "FAO" or "Faro".
        pickup_date: YYYY-MM-DD.
        pickup_time: HH:MM, 24-hour.
        dropoff_date: YYYY-MM-DD.
        dropoff_time: HH:MM, 24-hour.
        dropoff_location: Leave empty to return to the pick-up location.
        driver_age: Main driver's age (18-99); under 25 may add a fee.
    """
    user, err = mcp_auth.require()
    if err:
        return err
    return service.search_cars(pickup_location, pickup_date, pickup_time, dropoff_date,
                               dropoff_time, dropoff_location, driver_age, user_id=user["id"])


@mcp.tool()
def book_car(offer_id: str, driver_given_name: str, driver_family_name: str, phone: str) -> dict:
    """Book a rental car offer from search_cars for the main driver.

    Args:
        offer_id: An offer_id from search_cars, e.g. "CO-1A2B3C4D".
        driver_given_name: Main driver's first name.
        driver_family_name: Main driver's surname.
        phone: Contact phone in international format.
    """
    user, err = mcp_auth.require()
    if err:
        return err
    return service.book_car(offer_id, driver_given_name, driver_family_name, user["email"], phone,
                            user_id=user["id"])


# ---------------------------------------------------------------- trains

@mcp.tool()
def search_trains(origin: str, destination: str, departure_date: str, earliest_time: str = "06:00",
                  adults: int = 1, travel_class: str = "standard") -> dict:
    """Search train journeys departing after a time. Returns offers with an offer_id and price.

    Args:
        origin: Station or city name, e.g. "London St Pancras" or "Paris".
        destination: Station or city name.
        departure_date: YYYY-MM-DD.
        earliest_time: Earliest departure, HH:MM 24-hour.
        adults: Number of adult passengers (1-9).
        travel_class: standard or first.
    """
    user, err = mcp_auth.require()
    if err:
        return err
    return service.search_trains(origin, destination, departure_date, earliest_time, adults,
                                 travel_class, user_id=user["id"])


@mcp.tool()
def book_train(offer_id: str, passengers: list[TravellerName], phone: str) -> dict:
    """Book a train offer from search_trains. Needs one name per passenger on the offer.

    Args:
        offer_id: An offer_id from search_trains, e.g. "TO-1A2B3C4D".
        passengers: Each passenger's given and family name.
        phone: Contact phone in international format.
    """
    user, err = mcp_auth.require()
    if err:
        return err
    return service.book_train(offer_id, [p.model_dump() for p in passengers], user["email"], phone,
                              user_id=user["id"])


# ---------------------------------------------------------------- manage

@mcp.tool()
def find_my_trips() -> dict:
    """List the signed-in user's confirmed flight, hotel, car and train bookings."""
    user, err = mcp_auth.require()
    if err:
        return err
    return service.find_trips(user["email"])


@mcp.tool()
def cancel_trip(booking_id: str) -> dict:
    """Cancel one of the signed-in user's travel bookings by id (e.g. TR-1A2B3C4D).
    Refund depends on the fare rules."""
    user, err = mcp_auth.require()
    if err:
        return err
    return service.cancel_trip(booking_id, user["email"], any_owner=user["role"] == "admin")


@mcp.tool()
def admin_list_trips() -> dict:
    """Admin only: list the most recent confirmed trips for all customers."""
    _, err = mcp_auth.require("admin")
    if err:
        return err
    return service.all_trips()


db.init_db()
auth.init()
store.init()

if __name__ == "__main__":
    if "--http" in sys.argv:
        if hasattr(mcp, "settings") and hasattr(mcp.settings, "port"):  # MCP SDK 1.x
            mcp.settings.port = HTTP_PORT
            mcp.run(transport="streamable-http")
        else:  # MCP SDK 2.x
            mcp.run(transport="streamable-http", port=HTTP_PORT)
    else:
        mcp.run(transport="stdio")
