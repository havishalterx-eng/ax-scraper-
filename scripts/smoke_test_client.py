"""Connects to browser-mcp as a real MCP client over stdio and drives it.

Not a unit test — a protocol-level smoke test proving tool registration,
JSON schema generation, and stdio framing actually work end to end.
"""

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "browser_mcp.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()

            tools = await client.list_tools()
            print("=== TOOLS REGISTERED ===")
            for t in tools.tools:
                print(f"- {t.name}: {t.description}")

            print("\n=== CALLING browser_open ===")
            result = await client.call_tool("browser_open", {"url": "https://example.com"})
            for block in result.content:
                print(getattr(block, "text", block))

            print("\n=== CALLING click(1) ===")
            result = await client.call_tool("click", {"index": 1})
            for block in result.content:
                print(getattr(block, "text", block))

            await client.call_tool("close_browser", {})


if __name__ == "__main__":
    asyncio.run(main())
