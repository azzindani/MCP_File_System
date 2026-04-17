"""Progress message helpers for consistent MCP response formatting."""


def ok(msg: str, detail: str = "") -> dict:
    return {"icon": "\u2714", "msg": msg, "detail": detail}


def fail(msg: str, detail: str = "") -> dict:
    return {"icon": "\u2718", "msg": msg, "detail": detail}


def info(msg: str, detail: str = "") -> dict:
    return {"icon": "\u2139", "msg": msg, "detail": detail}


def warn(msg: str, detail: str = "") -> dict:
    return {"icon": "\u26a0", "msg": msg, "detail": detail}


def undo(msg: str, detail: str = "") -> dict:
    return {"icon": "\u21a9", "msg": msg, "detail": detail}
