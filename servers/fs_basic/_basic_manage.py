"""fs_manage implementation — disk usage, permissions, symlinks, versions."""
import shutil
import stat as stat_mod
from pathlib import Path

from _basic_helpers import (
    _error,
    get_platform,
    list_versions,
    ok,
    resolve_path,
)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_fs_manage(action: str, path: str = "") -> dict:
    try:
        return _fs_manage(action, path)
    except ValueError as e:
        return _error("fs_manage", str(e),
                      "Ensure path is within your home directory.")
    except PermissionError as e:
        return _error("fs_manage", f"Permission denied: {e}",
                      "Check permissions or choose a path you own.")
    except Exception as e:
        return _error("fs_manage", str(e),
                      "Use fs_manage with action=disk_usage for a simpler query.")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _fs_manage(action: str, path: str) -> dict:
    if action not in ("disk_usage", "permissions", "symlink_info", "versions"):
        return _error("fs_manage", f"Unknown action '{action}'",
                      "Use one of: disk_usage, permissions, symlink_info, versions.")

    if action == "disk_usage":
        return _action_disk_usage(path)
    if action == "permissions":
        return _action_permissions(path)
    if action == "symlink_info":
        return _action_symlink_info(path)
    # versions
    return _action_versions(path)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def _action_disk_usage(path: str) -> dict:
    target = resolve_path(path or str(Path.home()))
    try:
        usage = shutil.disk_usage(str(target))
    except Exception as e:
        return _error("fs_manage", f"Cannot get disk usage: {e}",
                      "Ensure the path exists and is accessible.")

    result: dict = {
        "success": True,
        "op": "fs_manage",
        "action": "disk_usage",
        "path": str(target),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "total_gb": round(usage.total / 1e9, 2),
        "used_gb": round(usage.used / 1e9, 2),
        "free_gb": round(usage.free / 1e9, 2),
        "used_percent": round(usage.used / usage.total * 100, 1) if usage.total else 0,
        "progress": [ok(f"Disk usage for {target.name}")],
    }
    result["token_estimate"] = len(str(result)) // 4
    return result


def _action_permissions(path: str) -> dict:
    if not path:
        return _error("fs_manage", "path required for action=permissions",
                      "Provide the file or directory path.")

    target = resolve_path(path, must_exist=True)
    st = target.lstat()
    mode_str = stat_mod.filemode(st.st_mode)
    platform = get_platform()

    result: dict = {
        "success": True,
        "op": "fs_manage",
        "action": "permissions",
        "path": str(target),
        "mode_string": mode_str,
        "mode_octal": oct(stat_mod.S_IMODE(st.st_mode)),
        "platform": platform,
        "progress": [ok(f"Permissions for {target.name}", mode_str)],
    }

    if platform in ("linux", "macos"):
        import grp
        import pwd
        try:
            result["owner"] = pwd.getpwuid(st.st_uid).pw_name
        except (KeyError, ImportError):
            result["owner"] = str(st.st_uid)
        try:
            result["group"] = grp.getgrgid(st.st_gid).gr_name
        except (KeyError, ImportError):
            result["group"] = str(st.st_gid)
    else:
        result["note"] = "Windows: POSIX permissions not applicable"

    result["token_estimate"] = len(str(result)) // 4
    return result


def _action_symlink_info(path: str) -> dict:
    if not path:
        return _error("fs_manage", "path required for action=symlink_info",
                      "Provide the path to inspect.")

    # Build raw (unresolved) path for accurate is_symlink check
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = Path.home() / raw

    if not raw.exists() and not raw.is_symlink():
        return _error("fs_manage", f"Path does not exist: {raw.name}",
                      "Use fs_query to locate the file first.")

    # Validate within home (resolve_path resolves symlink for security check)
    try:
        resolve_path(path)
    except ValueError as e:
        return _error("fs_manage", str(e),
                      "Ensure path is within your home directory.")
    except FileNotFoundError:
        pass  # OK for broken symlinks — raw check above already passed

    is_symlink = raw.is_symlink()
    symlink_target: str | None = None
    is_broken = False

    if is_symlink:
        try:
            symlink_target = str(raw.readlink())
        except (OSError, AttributeError):
            try:
                symlink_target = str(Path(path).resolve())
            except Exception:
                symlink_target = None
        is_broken = not raw.exists()

    result: dict = {
        "success": True,
        "op": "fs_manage",
        "action": "symlink_info",
        "path": str(raw),
        "is_symlink": is_symlink,
        "symlink_target": symlink_target,
        "is_broken": is_broken,
        "progress": [ok(f"Symlink info for {raw.name}")],
    }
    result["token_estimate"] = len(str(result)) // 4
    return result


def _action_versions(path: str) -> dict:
    if not path:
        return _error("fs_manage", "path required for action=versions",
                      "Provide the file path to list snapshots for.")

    file_path = resolve_path(path)
    versions = list_versions(str(file_path))

    result: dict = {
        "success": True,
        "op": "fs_manage",
        "action": "versions",
        "file": str(file_path),
        "versions": versions,
        "count": len(versions),
        "progress": [ok(f"Found {len(versions)} snapshot(s) for {file_path.name}")],
    }
    if not versions:
        result["hint"] = (
            "No snapshots found. Snapshots are created automatically on destructive writes."
        )
    result["token_estimate"] = len(str(result)) // 4
    return result
