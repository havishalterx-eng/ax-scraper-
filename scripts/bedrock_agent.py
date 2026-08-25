"""Drives browser-mcp using a Bedrock-hosted Claude model as the agent brain.

browser-mcp itself doesn't change for this - it's still the same MCP server
with the same tools. This script is the missing piece: it asks Bedrock what
to do, calls the real MCP tool, feeds the result back, and repeats until the
model says it's done. Same loop Claude Code runs internally when it calls an
MCP server - just made explicit and pointed at Bedrock instead.

Usage:
    .venv/bin/python scripts/bedrock_agent.py "go to news.ycombinator.com and tell me the top story title"

Model defaults to Mistral Large, not Claude - this AWS account's Anthropic
Marketplace subscription currently rejects calls with INVALID_PAYMENT_INSTRUMENT
(confirmed live, every Anthropic model on Bedrock affected, non-Anthropic
models unaffected). Once that's fixed in the AWS console, switch back with
BEDROCK_MODEL_ID=apac.anthropic.claude-3-7-sonnet-20250219-v1:0 (or newer, if
enabled) - the tool-use loop below doesn't care which model it's talking to.
"""

import asyncio
import json
import os
import sys

import boto3
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

DEFAULT_MODEL_ID = "mistral.mistral-large-2402-v1:0"
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
REGION = os.environ.get("AWS_REGION", "ap-south-1")
MAX_STEPS = 15

SYSTEM_PROMPT = (
    "You control a real web browser through tools. Every tool call returns "
    "the current page as a list of indexed interactive elements, like "
    "[3]<button> Sign In. Use that index number with click/type_text - "
    "never guess an index that wasn't just shown to you. Re-read the "
    "returned state after every action, since indices change when the page "
    "changes. If you call request_human_help, the tool result means the "
    "human has ALREADY finished - your next reply must call the next real "
    "tool needed to keep going, not describe what you're about to do. Only "
    "stop calling tools once the original task is actually complete, then "
    "reply with plain text."
)


def mcp_tools_to_bedrock(mcp_tools) -> list[dict]:
    tools = []
    for t in mcp_tools:
        schema = t.input_schema or {"type": "object", "properties": {}}
        tools.append(
            {
                "toolSpec": {
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": {"json": schema},
                }
            }
        )
    return tools


async def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: bedrock_agent.py "<task>"')
        sys.exit(1)
    task = sys.argv[1]

    bedrock = boto3.client("bedrock-runtime", region_name=REGION)

    params = StdioServerParameters(command=sys.executable, args=["-m", "browser_mcp.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as mcp_client:
            await mcp_client.initialize()
            mcp_tools = (await mcp_client.list_tools()).tools
            tool_config = {"tools": mcp_tools_to_bedrock(mcp_tools)}
            print(f"[agent] {len(mcp_tools)} tools available, model={MODEL_ID}\n")

            messages = [{"role": "user", "content": [{"text": task}]}]

            for step in range(1, MAX_STEPS + 1):
                resp = bedrock.converse(
                    modelId=MODEL_ID,
                    system=[{"text": SYSTEM_PROMPT}],
                    messages=messages,
                    toolConfig=tool_config,
                )
                out_message = resp["output"]["message"]
                messages.append(out_message)

                tool_uses = [b["toolUse"] for b in out_message["content"] if "toolUse" in b]
                texts = [b["text"] for b in out_message["content"] if "text" in b]
                for t in texts:
                    print(f"[model] {t}")

                if resp["stopReason"] != "tool_use":
                    print("\n[agent] done.")
                    return

                result_content = []
                for tu in tool_uses:
                    name, args, use_id = tu["name"], tu.get("input", {}), tu["toolUseId"]
                    print(f"[tool call] {name}({json.dumps(args)})")
                    result = await mcp_client.call_tool(name, args)
                    text_out = "\n".join(getattr(b, "text", str(b)) for b in result.content)
                    print(f"[tool result] {text_out[:300]}\n")
                    if name == "request_human_help":
                        input("[agent] paused for human_help - press Enter once done > ")
                        text_out += (
                            "\n\nThe human has just confirmed this step is complete. "
                            "Call the next real tool right now to continue the original "
                            "task - do not just describe what you plan to do."
                        )
                    result_content.append(
                        {"toolResult": {"toolUseId": use_id, "content": [{"text": text_out}]}}
                    )
                messages.append({"role": "user", "content": result_content})

            print(f"\n[agent] stopped after {MAX_STEPS} steps without finishing.")


if __name__ == "__main__":
    asyncio.run(main())
