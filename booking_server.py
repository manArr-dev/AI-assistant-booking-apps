"""
Step 3 — The MCP server.

Exposes the booking operations as MCP tools. Any MCP client can use them:
your LangChain agent, Claude Desktop, an IDE, etc.

Every tool requires a signed-in user (see mcp_auth.py). The customer's name and
email come from their session, not from tool arguments, so a customer can only
see and cancel their own bookings. admin_* tools need the admin role.

Run modes:
    python booking_server.py           # stdio (the agent launches it, passing the session)
    python booking_server.py --http    # streamable HTTP on http://localhost:8000/mcp,
                                       # requires  Authorization: Bearer <session token>
"""
import sys

from dotenv import load_dotenv

try:
    # MCP Python SDK 2.x renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:
    # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as Server

load_dotenv()

import auth  # noqa: E402
import db  # noqa: E402
import mcp_auth  # noqa: E402

HTTP_PORT = 8000
mcp = Server("booking", **mcp_auth.server_options(HTTP_PORT))


@mcp.tool()
def list_services() -> dict:
    """List the services customers can book, with id, name, duration in minutes and price.
    Call this first to get valid service ids."""
    _, err = mcp_auth.require()
    if err:
        return err
    return {"ok": True, "services": db.list_services()}


@mcp.tool()
def check_availability(service_id: str, date: str) -> dict:
    """Return the free start times for a service on a date.

    Args:
        service_id: A service id from list_services, e.g. "haircut".
        date: The day in YYYY-MM-DD format.
    """
    _, err = mcp_auth.require()
    if err:
        return err
    return db.available_slots(service_id, date)


@mcp.tool()
def book_appointment(service_id: str, date: str, start_time: str) -> dict:
    """Book an appointment for the signed-in customer. Only use a start_time returned
    by check_availability.

    Args:
        service_id: A service id from list_services.
        date: The day in YYYY-MM-DD format.
        start_time: Start time in 24-hour HH:MM format, e.g. "14:30".
    """
    user, err = mcp_auth.require()
    if err:
        return err
    return db.create_booking(service_id, date, start_time, user["full_name"], user["email"])


@mcp.tool()
def find_my_bookings() -> dict:
    """List the signed-in customer's upcoming confirmed bookings."""
    user, err = mcp_auth.require()
    if err:
        return err
    return db.find_bookings(user["email"])


@mcp.tool()
def cancel_booking(booking_id: str) -> dict:
    """Cancel one of the signed-in customer's bookings by id (e.g. BK-1A2B3C4D)."""
    user, err = mcp_auth.require()
    if err:
        return err
    return db.cancel_booking(booking_id, user["email"], any_owner=user["role"] == "admin")


@mcp.tool()
def admin_list_bookings(date: str) -> dict:
    """Admin only: list every confirmed booking on a date (YYYY-MM-DD), with customer emails."""
    _, err = mcp_auth.require("admin")
    if err:
        return err
    return db.bookings_on(date)


db.init_db()
auth.init()

if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
