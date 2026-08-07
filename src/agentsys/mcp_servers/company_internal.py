"""A small, real MCP server -- the reference implementation this project's MCP
client support is proven against.

This is deliberately NOT a mock or an in-process fake. It runs as its own OS
process and speaks the actual Model Context Protocol over stdio, exactly the way
a third party's server would. That is the whole point: if AgentForge can call
these tools, the plugin backbone genuinely works, and connecting someone else's
server is a config entry rather than a code change (see settings.mcp_servers).

The two tools are chosen to be trivially verifiable from the outside, which
matters for testing a protocol boundary:
  - roll_dice is random, so a test can prove a real call happened rather than a
    cached or hardcoded value being echoed back.
  - get_current_time is information AgentForge has no other tool to obtain,
    so a task answering "what time is it" proves the MCP path was actually used.

Run directly (what the client spawns):  python -m agentsys.mcp_servers.demo_utility
"""

import random
from datetime import datetime, timezone

from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    name="company_internal",
    instructions="Internal company systems: office directory, plus small utilities.",
)

# NOTE: the server name matters more than it looks. It becomes the middle of
# every tool's registered name (mcp_company_internal_lookup_office), which is
# part of what the specialist LLM reads when choosing. An earlier name here was
# "demo_utility", and the specialist reliably ignored these tools in favour of
# web_search even for a question only this server could answer -- its own logged
# rationale was that there were "no other context clues or prior knowledge
# provided", i.e. it discounted the tools outright. A name that reads as a
# placeholder invites being treated as one.


# NOTE (for maintainers, deliberately NOT in the docstrings below): a tool's
# docstring becomes its MCP description verbatim, and that description is the
# only thing the specialist LLM sees when choosing a tool. Design rationale,
# implementation notes, and anything else addressed to a human reader must stay
# in comments like this one -- put it in the docstring and it becomes noise that
# competes with the actual capability, which measurably degrades tool selection.
# Docstrings here therefore say only what the tool does and when to use it.
# They also omit an "Arguments:" list, because MCPTool renders the machine
# readable input schema into the description automatically and a hand-written
# second copy would duplicate and drift from it.


@mcp.tool()
def get_current_time(tz: str = "utc") -> dict:
    """Returns the current real-world date and time."""
    now = datetime.now(timezone.utc)
    return {
        "iso": now.isoformat(),
        "human": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "timezone": "utc",
    }


@mcp.tool()
def roll_dice(sides: int = 6, count: int = 1) -> dict:
    """Rolls one or more dice and returns each roll plus their total."""
    if sides < 2:
        raise ValueError("sides must be at least 2")
    if not 1 <= count <= 100:
        raise ValueError("count must be between 1 and 100")
    rolls = [random.randint(1, sides) for _ in range(count)]
    return {"rolls": rolls, "total": sum(rolls), "sides": sides}


_OFFICE_DIRECTORY = {
    "BLR-2": {"city": "Bengaluru", "country": "India", "floor_count": 7, "desks": 412},
    "AMS-1": {"city": "Amsterdam", "country": "Netherlands", "floor_count": 3, "desks": 96},
    "SFO-4": {"city": "San Francisco", "country": "USA", "floor_count": 5, "desks": 240},
}


@mcp.tool()
def lookup_office(office_code: str) -> dict:
    """Looks up one of this company's internal offices by its office code (for
    example 'BLR-2') and returns its city, country, floor count and desk
    capacity. This is the ONLY source for internal office information: it is
    not in the sample_metric database, not on the public web, and cannot be
    computed. Use this whenever a request mentions an office or an office code.
    """
    key = (office_code or "").strip().upper()
    if key not in _OFFICE_DIRECTORY:
        raise ValueError(
            f"unknown office code {office_code!r}; known codes: {sorted(_OFFICE_DIRECTORY)}"
        )
    return {"office_code": key, **_OFFICE_DIRECTORY[key]}


if __name__ == "__main__":
    mcp.run(transport="stdio")
