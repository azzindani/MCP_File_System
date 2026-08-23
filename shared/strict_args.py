"""Refuse an argument this server does not take, instead of ignoring it.

This server runs on the FastMCP bundled in the `mcp` SDK, which builds each
tool's argument model with pydantic's default `extra="ignore"`. The consequence
is not cosmetic:

    fs_query(path=..., pattern="*", type_="dir")   ->  0 results  (filter applied)
    fs_query(path=..., pattern="*", type="dir")    ->  6 results  (filter dropped)
    fs_query(path=..., pattern="*", nonsense=1)    ->  success: true

`type` is the name a caller would reach for -- the tool declares `type_`, a
trailing-underscore spelling that exists nowhere else in the fleet -- and the
wrong guess produces an unfiltered answer under `success: true`, with nothing
anywhere to read. Every typo behaves the same way.

The sibling repos run standalone fastmcp 2.x, which forbids extra arguments and
answers "Unexpected keyword argument". Three of the four servers therefore tell
a caller when it got a name wrong, and this one did not.

`enforce_known_arguments(mcp)` closes that gap by checking argument names
against the tool's own schema before dispatch. It improves on the pydantic
message while it is at it: the refusal *lists the names the tool accepts*, so a
caller can fix the call from the response rather than guessing again.

Applied once at server start; no tool body changes.
"""

from __future__ import annotations

import json
from typing import Any


def _did_you_mean(unknown: str, known: list[str]) -> str:
    """The closest accepted name, when one is obviously close."""
    import difflib

    # Underscore-insensitive first: type/type_ and format/format_ are the real
    # cases here and difflib alone rates them no higher than unrelated names.
    stripped = {k.rstrip("_"): k for k in known}
    if unknown.rstrip("_") in stripped:
        return stripped[unknown.rstrip("_")]
    close = difflib.get_close_matches(unknown, known, n=1, cutoff=0.75)
    return close[0] if close else ""


def enforce_known_arguments(mcp: Any) -> None:
    """Make every tool on this server refuse an argument it does not declare."""
    manager = mcp._tool_manager
    original = manager.call_tool

    async def call_tool(name: str, arguments: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        tool = manager.get_tool(name)
        if tool is not None and isinstance(arguments, dict):
            known = sorted((tool.parameters or {}).get("properties", {}))
            unknown = [k for k in arguments if k not in known]
            if unknown and known:
                first = unknown[0]
                suggestion = _did_you_mean(first, known)
                hint = (
                    f"Did you mean {suggestion}=?"
                    if suggestion
                    else f"{name} accepts: {', '.join(known)}."
                )
                return json.dumps(
                    {
                        "success": False,
                        "op": name,
                        "error": f"{name} does not take {', '.join(unknown)}",
                        "hint": f"{hint} Accepted: {', '.join(known)}.",
                        "progress": [],
                        "token_estimate": 40,
                    }
                )
        return await original(name, arguments, *args, **kwargs)

    manager.call_tool = call_tool
