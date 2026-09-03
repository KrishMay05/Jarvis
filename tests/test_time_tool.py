from datetime import datetime
from zoneinfo import ZoneInfo

from src.tools.time_tool import TimeTool


def test_time_tool_uses_named_timezone():
    result = TimeTool().use("America/New_York")
    now = datetime.now(ZoneInfo("America/New_York"))
    assert "America/New_York" in result
    assert now.strftime("%Y-%m-%d") in result


def test_time_tool_accepts_dict_args():
    result = TimeTool().use({"timezone": "UTC"})
    assert "UTC" in result


def test_time_tool_rejects_unknown_timezone():
    result = TimeTool().use("Not/A_Zone")
    assert "Unknown timezone" in result


def test_time_tool_falls_back_to_local_time():
    result = TimeTool().use("")
    assert "current local time" in result
