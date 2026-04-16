"""CI check: verify all MCP tool docstrings are ≤80 characters.

Usage:  python verify_tool_docstrings.py
Exit 0 on success, 1 on failure.
"""
import ast
import sys
from pathlib import Path


def _check_file(path: Path) -> list[str]:
    """Return list of violation messages for tool docstrings in path."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as e:
        return [f"{path}: SyntaxError: {e}"]

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        # Check if decorated with @mcp.tool(...)
        is_tool = any(
            (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
             and d.func.attr == "tool")
            or (isinstance(d, ast.Attribute) and d.attr == "tool")
            for d in node.decorator_list
        )
        if not is_tool:
            continue
        docstring = ast.get_docstring(node)
        if not docstring:
            violations.append(
                f"{path}:{node.lineno}: tool '{node.name}' has no docstring"
            )
            continue
        first_line = docstring.splitlines()[0]
        if len(first_line) > 80:
            violations.append(
                f"{path}:{node.lineno}: tool '{node.name}' docstring "
                f"first line is {len(first_line)} chars (max 80): "
                f"'{first_line}'"
            )
    return violations


def main() -> int:
    root = Path(__file__).parent
    server_files = list(root.glob("servers/**/server.py"))
    if not server_files:
        print("No server.py files found.", file=sys.stderr)
        return 0

    all_violations: list[str] = []
    for f in server_files:
        all_violations.extend(_check_file(f))

    if all_violations:
        print("FAIL — tool docstring violations found:", file=sys.stderr)
        for v in all_violations:
            print(f"  {v}", file=sys.stderr)
        return 1

    print(f"OK — checked {len(server_files)} server file(s), all docstrings ≤80 chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
