from datetime import datetime, timezone
import zoneinfo

from agent.tool_registry import tool


@tool(
    name="get_datetime",
    description="Get the current date and time, optionally in a specific timezone.",
    parameters={
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": (
                    "IANA timezone name, e.g. 'America/New_York', 'Asia/Kolkata', "
                    "'Europe/London'. Leave empty for local system time."
                ),
            }
        },
        "required": [],
    },
)
def get_datetime(timezone: str = "") -> str:
    try:
        if timezone:
            tz = zoneinfo.ZoneInfo(timezone)
            now = datetime.now(tz)
        else:
            now = datetime.now()
        return now.strftime("%A, %B %d %Y  %I:%M %p %Z").strip()
    except Exception as e:
        return f"Error: {e}"
