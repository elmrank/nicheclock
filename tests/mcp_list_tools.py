"""Start `nicheclock mcp` over stdio, ask for list_tools and, when given, call one tool.

    python tests/mcp_list_tools.py                      # prints the tool names, exits 1 if any is missing
    python tests/mcp_list_tools.py upcoming example     # also calls upcoming(project="example")
    NC_MCP_CMD="docker compose run --rm -T nicheclock mcp" python tests/mcp_list_tools.py   # test another launch command

Used by the docs-smoke CI job; needs `pip install 'nicheclock[mcp]'` and a config (NICHECLOCK_CONFIG or ./nicheclock.yaml).
"""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED = {"trends_for_niche", "upcoming", "gsc_gaps", "explain_topic"}


async def main(call: list[str]) -> int:
    cmd = os.environ.get("NC_MCP_CMD", "").split() or [sys.executable, "-m", "nicheclock", "mcp"]
    params = StdioServerParameters(command=cmd[0], args=cmd[1:], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            print("tools:", ", ".join(sorted(tools)))
            if not EXPECTED <= tools:
                print("missing:", ", ".join(sorted(EXPECTED - tools)), file=sys.stderr)
                return 1
            if call:
                res = await session.call_tool(call[0], {"project": call[1]})
                failed = bool(getattr(res, "is_error", getattr(res, "isError", False)))   # name differs across mcp versions
                print(call[0], "->", "error" if failed else "ok", str(res.content)[:200])
                return 1 if failed else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
