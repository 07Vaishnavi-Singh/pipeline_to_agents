from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from coordinator import run_request

mcp = FastMCP("recepto-mcp")


@mcp.tool()
def recommend_play(query: str, session_id: str = "default") -> str:
    result = run_request(query, tool_name="recommend_play", session_id=session_id)
    return result.get("final_response") or "No recommendation produced."


@mcp.tool()
def create_play(query: str, session_id: str = "default") -> str:
    result = run_request(query, tool_name="create_play", session_id=session_id)
    return result.get("final_response") or "No play created."


@mcp.tool()
def recommend_outreach(query: str, session_id: str = "default") -> str:
    result = run_request(query, tool_name="recommend_outreach", session_id=session_id)
    return result.get("final_response") or "No outreach strategy produced."


@mcp.tool()
def analyze_account(query: str, session_id: str = "default") -> str:
    result = run_request(query, tool_name="analyze_account", session_id=session_id)
    return result.get("final_response") or "No account analysis produced."


@mcp.tool()
def ask_recepto(query: str, session_id: str = "default") -> str:
    result = run_request(query, tool_name="ask_recepto", session_id=session_id)
    return result.get("final_response") or "No response produced."


if __name__ == "__main__":
    mcp.run()
