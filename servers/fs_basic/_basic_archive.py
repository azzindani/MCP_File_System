"""fs_archive implementation — zip/tar.gz create, extract, list."""

import tarfile
import zipfile
from pathlib import Path

from _basic_helpers import (
    _error,
    get_default_output_dir,
    info,
    ok,
    resolve_path,
)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_fs_archive(
    action: str,
    path: str,
    target: str = "",
    format_: str = "zip",
    dry_run: bool = False,
) -> dict:
    try:
        return _fs_archive(action, path, target, format_, dry_run)
    except ValueError as e:
        return _error("fs_archive", str(e), "Ensure all paths are within your home directory.")
    except Exception as e:
        return _error("fs_archive", str(e), "Check archive path, target, and format then retry.")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _fs_archive(action: str, path: str, target: str, format_: str, dry_run: bool) -> dict:
    if action not in ("create", "extract", "list"):
        return _error(
            "fs_archive", f"Unknown action '{action}'", "Use one of: create, extract, list."
        )
    _format_aliases = {"tar": "tar.gz", "tgz": "tar.gz", "gz": "tar.gz", "gzip": "tar.gz"}
    format_ = _format_aliases.get(format_, format_)
    if format_ not in ("zip", "tar.gz"):
        return _error("fs_archive", f"Unknown format '{format_}'", "Use 'zip' or 'tar.gz'.")

    if action == "create":
        return _action_create(path, target, format_, dry_run)
    if action == "extract":
        return _action_extract(path, target, dry_run)
    # list
    return _action_list(path)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def _action_create(archive_path: str, source: str, format_: str, dry_run: bool) -> dict:
    if not source:
        return _error(
            "fs_archive",
            "target (source) required for action=create",
            "Provide the file or directory to archive in 'target'.",
        )

    arc = resolve_path(archive_path)
    src = resolve_path(source, must_exist=True)
    progress = []

    # Count items
    if src.is_dir():
        items = [p for p in src.rglob("*") if p.is_file()]
    else:
        items = [src]

    progress.append(info(f"Archiving {len(items)} file(s) from {src.name}"))

    if dry_run:
        result: dict = {
            "success": True,
            "op": "fs_archive",
            "action": "create",
            "archive": str(arc),
            "source": str(src),
            "format": format_,
            "would_include": len(items),
            "dry_run": True,
            "progress": progress,
        }
        result["token_estimate"] = len(str(result)) // 4
        return result

    arc.parent.mkdir(parents=True, exist_ok=True)

    if format_ == "zip":
        with zipfile.ZipFile(arc, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if src.is_dir():
                for item in items:
                    zf.write(item, item.relative_to(src.parent))
            else:
                zf.write(src, src.name)
    else:  # tar.gz
        with tarfile.open(arc, "w:gz") as tf:
            tf.add(str(src), arcname=src.name)

    size_kb = arc.stat().st_size // 1024
    progress.append(ok(f"Created {arc.name}", f"{size_kb} KB"))

    result = {
        "success": True,
        "op": "fs_archive",
        "action": "create",
        "archive": str(arc),
        "source": str(src),
        "format": format_,
        "files_archived": len(items),
        "size_kb": size_kb,
        "progress": progress,
    }
    result["token_estimate"] = len(str(result)) // 4
    return result


def _action_extract(archive_path: str, target: str, dry_run: bool) -> dict:
    arc = resolve_path(archive_path, must_exist=True)
    out_dir = resolve_path(target or str(get_default_output_dir(archive_path)))
    progress = []

    # Detect format from extension
    name_lower = arc.name.lower()
    if name_lower.endswith(".zip"):
        return _extract_zip(arc, out_dir, dry_run, progress)
    if name_lower.endswith((".tar.gz", ".tgz")):
        return _extract_targz(arc, out_dir, dry_run, progress)
    return _error(
        "fs_archive",
        f"Cannot detect archive format from filename '{arc.name}'",
        "Rename the archive to end in .zip, .tar.gz, or .tgz.",
    )


def _extract_zip(arc: Path, out_dir: Path, dry_run: bool, progress: list) -> dict:
    try:
        with zipfile.ZipFile(arc, "r") as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return _error(
            "fs_archive",
            f"Not a valid zip file: {arc.name}",
            "Verify the archive is not corrupted.",
        )

    # Check for conflicts
    conflicts = [n for n in names if (out_dir / n).exists()]
    if conflicts:
        return _error(
            "fs_archive",
            f"{len(conflicts)} file(s) would be overwritten in {out_dir.name}",
            "Pass overwrite=True or choose a different target directory.",
            {"conflicts": conflicts[:10]},
        )

    progress.append(info(f"Extracting {len(names)} entries to {out_dir.name}"))

    if dry_run:
        result: dict = {
            "success": True,
            "op": "fs_archive",
            "action": "extract",
            "archive": str(arc),
            "target": str(out_dir),
            "would_extract": len(names),
            "dry_run": True,
            "progress": progress,
        }
        result["token_estimate"] = len(str(result)) // 4
        return result

    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(arc, "r") as zf:
        zf.extractall(out_dir)

    progress.append(ok(f"Extracted {len(names)} files to {out_dir.name}"))
    result = {
        "success": True,
        "op": "fs_archive",
        "action": "extract",
        "archive": str(arc),
        "target": str(out_dir),
        "extracted": len(names),
        "progress": progress,
    }
    result["token_estimate"] = len(str(result)) // 4
    return result


def _extract_targz(arc: Path, out_dir: Path, dry_run: bool, progress: list) -> dict:
    try:
        with tarfile.open(arc, "r:gz") as tf:
            members = tf.getnames()
    except tarfile.TarError as e:
        return _error(
            "fs_archive", f"Not a valid tar.gz: {e}", "Verify the archive is not corrupted."
        )

    conflicts = [m for m in members if (out_dir / m).exists()]
    if conflicts:
        return _error(
            "fs_archive",
            f"{len(conflicts)} file(s) would be overwritten in {out_dir.name}",
            "Pass overwrite=True or choose a different target directory.",
            {"conflicts": conflicts[:10]},
        )

    progress.append(info(f"Extracting {len(members)} entries to {out_dir.name}"))

    if dry_run:
        result: dict = {
            "success": True,
            "op": "fs_archive",
            "action": "extract",
            "archive": str(arc),
            "target": str(out_dir),
            "would_extract": len(members),
            "dry_run": True,
            "progress": progress,
        }
        result["token_estimate"] = len(str(result)) // 4
        return result

    out_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(arc, "r:gz") as tf:
        tf.extractall(out_dir, filter="data")

    progress.append(ok(f"Extracted {len(members)} files to {out_dir.name}"))
    result = {
        "success": True,
        "op": "fs_archive",
        "action": "extract",
        "archive": str(arc),
        "target": str(out_dir),
        "extracted": len(members),
        "progress": progress,
    }
    result["token_estimate"] = len(str(result)) // 4
    return result


def _action_list(archive_path: str) -> dict:
    arc = resolve_path(archive_path, must_exist=True)
    name_lower = arc.name.lower()

    if name_lower.endswith(".zip"):
        try:
            with zipfile.ZipFile(arc, "r") as zf:
                entries = [
                    {
                        "name": info_obj.filename,
                        "size": info_obj.file_size,
                        "compressed": info_obj.compress_size,
                        "is_dir": info_obj.filename.endswith("/"),
                    }
                    for info_obj in zf.infolist()
                ]
        except zipfile.BadZipFile:
            return _error(
                "fs_archive", f"Not a valid zip: {arc.name}", "Verify the archive is not corrupted."
            )
    elif name_lower.endswith((".tar.gz", ".tgz")):
        try:
            with tarfile.open(arc, "r:gz") as tf:
                entries = [
                    {
                        "name": m.name,
                        "size": m.size,
                        "is_dir": m.isdir(),
                    }
                    for m in tf.getmembers()
                ]
        except tarfile.TarError as e:
            return _error(
                "fs_archive", f"Not a valid tar.gz: {e}", "Verify the archive is not corrupted."
            )
    else:
        return _error(
            "fs_archive",
            f"Cannot detect archive format: {arc.name}",
            "Rename to end in .zip, .tar.gz, or .tgz.",
        )

    result: dict = {
        "success": True,
        "op": "fs_archive",
        "action": "list",
        "archive": str(arc),
        "entries": entries,
        "count": len(entries),
        "progress": [ok(f"Listed {len(entries)} entries in {arc.name}")],
    }
    result["token_estimate"] = len(str(result)) // 4
    return result
