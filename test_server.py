"""
Step 4 — Test the MCP server directly, without any LLM.

Launches booking_server.py over stdio as different signed-in users and checks:
book -> double-book -> find -> cancel, plus authorization: anonymous calls are
refused, a customer can't see or cancel someone else's booking, an admin can.
Uses a throwaway database.

    python test_server.py
"""
import asyncio
from datetime import date, timedelta

from test_helpers import call, make_user, mcp_session

SERVER = "booking_server.py"


def next_weekday() -> str:
    d = date.today() + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


async def main():
    alice = make_user("alice@example.com", "Alice Test")
    bob = make_user("bob@example.com", "Bob Test")
    admin = make_user("admin@example.com", "Ada Admin", role="admin")
    day = next_weekday()

    async with mcp_session(SERVER, None) as anon:
        print("Tools:", [t.name for t in (await anon.list_tools()).tools])
        denied = await call(anon, "list_services")
        assert not denied["ok"], "anonymous calls must be refused"

    async with mcp_session(SERVER, alice) as s:
        await call(s, "list_services")
        slots = await call(s, "check_availability", service_id="haircut", date=day)
        first = slots["available_times"][0]
        booked = await call(s, "book_appointment", service_id="haircut", date=day, start_time=first)
        assert booked["ok"], booked
        mine = await call(s, "find_my_bookings")
        assert [b["booking_id"] for b in mine["bookings"]] == [booked["booking_id"]]
        assert mine["bookings"][0]["customer_name"] == "Alice Test", "name comes from the account"
        forbidden = await call(s, "admin_list_bookings", date=day)
        assert not forbidden["ok"], "customers can't use admin tools"

    async with mcp_session(SERVER, bob) as s:
        dup = await call(s, "book_appointment", service_id="haircut", date=day, start_time=first)
        assert not dup["ok"], "double booking should be rejected"
        theirs = await call(s, "find_my_bookings")
        assert theirs["bookings"] == [], "Bob must not see Alice's booking"
        steal = await call(s, "cancel_booking", booking_id=booked["booking_id"])
        assert not steal["ok"], "Bob must not cancel Alice's booking"

    async with mcp_session(SERVER, admin) as s:
        everyone = await call(s, "admin_list_bookings", date=day)
        assert [b["booking_id"] for b in everyone["bookings"]] == [booked["booking_id"]]
        second = (await call(s, "check_availability", service_id="haircut", date=day))["available_times"][0]

    async with mcp_session(SERVER, alice) as s:
        other = await call(s, "book_appointment", service_id="haircut", date=day, start_time=second)
        ok = await call(s, "cancel_booking", booking_id=booked["booking_id"])
        assert ok["ok"], ok

    async with mcp_session(SERVER, admin) as s:
        ok = await call(s, "cancel_booking", booking_id=other["booking_id"])
        assert ok["ok"], "admins may cancel any booking"

    print("\nAll server checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
