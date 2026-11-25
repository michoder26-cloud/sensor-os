"""A tiny MCP server (stdio) that Claude Code asks before it uses one of your services for JARVIS.

Claude Code is started with `--permission-prompt-tool mcp__jarvis__approve`, so every tool call that is not
already allowed comes here first. Looking things up (list / get / search / read ...) is allowed at once.
Anything else - sending, creating, changing, deleting, buying - is written to a request file; JARVIS asks
you out loud and writes your answer back. No answer in time = not allowed.

Run as: python approve_server.py <folder>   (the folder JARVIS and this server share)
"""
import json
import os
import re
import sys
import time

READ = re.compile(r"(^|[_\-.])(list|get|search|read|fetch|query|find|view|describe|count|check|lookup|status|info|help|"
                  r"show|preview|download|resolve|who_am_i|whoami|summar\w*)", re.I)
WAIT = 45.0                    # seconds JARVIS has to ask you and hear "confirm"


def is_read_only(tool: str) -> bool:
    """mcp__claude_ai_Gmail__search_threads -> True; ...__send_message -> False."""
    name = tool.split("__")[-1]
    return bool(READ.search(name)) and not re.search(r"(send|delete|remove|create|update|write|post|publish|trash|"
                                                     r"buy|purchase|pay|transfer|share|move|edit|set|add|cancel)", name, re.I)


def decide(folder: str, tool: str, args: dict) -> dict:
    if is_read_only(tool):
        return {"behavior": "allow", "updatedInput": args}
    n = f"{time.time():.3f}"
    req, resp = os.path.join(folder, "approve_req.json"), os.path.join(folder, "approve_resp.json")
    if os.path.exists(resp):
        os.remove(resp)
    with open(req + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"id": n, "tool": tool, "input": args}, f, ensure_ascii=False)
    os.replace(req + ".tmp", req)
    end = time.time() + WAIT
    while time.time() < end:
        if os.path.exists(resp):
            try:
                with open(resp, encoding="utf-8") as f:
                    answer = json.load(f)
            except (OSError, ValueError):
                time.sleep(0.1)
                continue
            if answer.get("id") == n:
                os.remove(resp)
                if answer.get("allow"):
                    return {"behavior": "allow", "updatedInput": args}
                return {"behavior": "deny", "message": "The user said no. Do not try again; tell them it was not done."}
        time.sleep(0.15)
    try:
        os.remove(req)
    except OSError:
        pass
    return {"behavior": "deny", "message": "The user did not confirm in time. Tell them it was not done."}


TOOL = {"name": "approve", "description": "Asks the user (by voice, through JARVIS) whether a tool may be used.",
        "inputSchema": {"type": "object", "properties": {"tool_name": {"type": "string"}, "input": {"type": "object"},
                                                         "tool_use_id": {"type": "string"}},
                        "required": ["tool_name", "input"]},
        # MCP tool hints: it only asks a question (no change anywhere), each call asks afresh, and it reaches
        # nothing outside this PC (the question goes to JARVIS through a file in the shared folder).
        "annotations": {"title": "Ask the user", "readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": False, "openWorldHint": False}}


def main() -> None:
    folder = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    sys.stdin.reconfigure(encoding="utf-8")            # Windows would read Claude's UTF-8 as cp1252: garbled Thai
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "id" not in msg:
            continue                                       # a notification
        method, mid = msg.get("method"), msg["id"]
        if method == "initialize":
            result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "jarvis", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [TOOL]}
        elif method == "tools/call":
            a = (msg.get("params") or {}).get("arguments") or {}
            verdict = decide(folder, str(a.get("tool_name", "")), a.get("input") or {})
            result = {"content": [{"type": "text", "text": json.dumps(verdict, ensure_ascii=False)}]}
        elif method == "ping":
            result = {}
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid,
                                         "error": {"code": -32601, "message": "unknown method"}}) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
