"""fs_archive implementation — zip/tar.gz create, extract, list."""

import os
import tarfile
import time
import zipfile
from pathlib import Path

from _basic_helpers import (
    _error,
    get_default_output_dir,
    info,
    ok,
    resolve_path,
    size_kb,
    warn,
)

from shared.version_control import snapshot

# Re-creating an archive over an older copy of itself is the normal case, so
# these suffixes are replaced without argument. Anything else is a destination
# the caller almost certainly did not mean to destroy.
_ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tgz", ".tar")

# A zip entry made on Unix carries its st_mode in the top half of external_attr.
# 0o120000 is S_IFLNK: the entry is a symlink and its *content* is the target
# path. tarfile has represented links this way all along; zipfile will do it
# too, but only if asked.
_S_IFLNK = 0o120000
_LINK_ATTR = (_S_IFLNK | 0o777) << 16


def _is_zip_symlink(zi: zipfile.ZipInfo) -> bool:
    return (zi.external_attr >> 16) & 0o170000 == _S_IFLNK


def _zip_symlink(zf: zipfile.ZipFile, link: Path, arcname: Path) -> None:
    """Store the link itself, the way tar.gz already does.

    zipfile.write() opens the path and reads it, which for a symlink means
    reading whatever it points at -- so archiving a folder used to copy the
    *contents* of files outside that folder into the zip.
    """
    zi = zipfile.ZipInfo(arcname.as_posix(), date_time=time.localtime(link.lstat().st_mtime)[:6])
    zi.create_system = 3  # Unix, so the mode bits above are honoured
    zi.external_attr = _LINK_ATTR
    zf.writestr(zi, os.readlink(link))


def _links_hint(links: list[Path], escaping: list[Path], src: Path) -> str:
    base = (
        f"{len(links)} symlink(s) are stored as links, so extracting elsewhere reproduces the "
        "links and not copies of their targets."
    )
    if not escaping:
        return base
    names = ", ".join(str(p.relative_to(src)) for p in escaping[:3])
    more = f" (+{len(escaping) - 3} more)" if len(escaping) > 3 else ""
    return (
        f"{base} {len(escaping)} of them point outside {src.name} -- {names}{more} -- so those "
        f"will dangle wherever the archive is unpacked. Copy the targets into {src.name} before "
        "archiving if the data itself needs to travel."
    )


def _escapes(link: Path, root: Path) -> bool:
    """Does this link resolve to somewhere outside the tree being archived?"""
    try:
        target = Path(os.path.join(link.parent, os.readlink(link))).resolve()
        return not target.is_relative_to(root.resolve())
    except OSError:
        return False


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
    except FileNotFoundError as e:
        # "Check archive path, target, and format" never said which of the three
        # was wrong. For extract and list the missing path is always `path`.
        return _error(
            "fs_archive",
            str(e),
            f"'path' must be an existing archive for action={action}. Use fs_query to locate it.",
        )
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

    # The swapped-argument guard further down needs both paths in hand, but
    # `source` used to be resolved with must_exist=True right here -- and in a
    # swapped call `source` holds the archive that does not exist *yet*, so
    # resolution raised FileNotFoundError first and the guard written for this
    # exact mistake never ran. The caller was told "Path does not exist:
    # <the .zip it asked us to create>" under a hint naming all three
    # arguments. Judge the two names before touching the filesystem.
    src = resolve_path(source)
    if not src.exists():
        src_is_archive = str(src).lower().endswith(_ARCHIVE_SUFFIXES)
        arc_is_archive = str(arc).lower().endswith(_ARCHIVE_SUFFIXES)
        if src_is_archive and not arc_is_archive:
            return _error(
                "fs_archive",
                f"'target' does not exist: {src.name}",
                "'path' is the archive to write and 'target' is what goes into it -- they look "
                f"swapped here. To create {src.name}, pass it as 'path' and give 'target' the "
                f"file or folder to archive (such as {arc.name}).",
            )
        return _error(
            "fs_archive",
            f"'target' does not exist: {src}",
            "'target' is the existing file or folder to archive and 'path' is the .zip or "
            ".tar.gz to create. Use fs_query to locate the file first.",
        )

    progress = []

    # Count items. Symlinks are kept apart from regular files: is_file() follows
    # them, so a link used to be archived as a copy of its target, and a link to
    # a *directory* answered False to both is_file() and (for rglob) recursion,
    # so it vanished from a zip with nothing said. Naming a symlink directly as
    # `target` still archives what it points at -- that is what the caller asked
    # for. This is about the ones swept up incidentally inside a folder.
    links: list[Path] = []
    if src.is_dir():
        items = []
        for p in src.rglob("*"):
            if p.is_symlink():
                links.append(p)
            elif p.is_file():
                items.append(p)
    else:
        items = [src]

    escaping = [p for p in links if _escapes(p, src)]

    progress.append(info(f"Archiving {len(items)} file(s) from {src.name}"))
    if links:
        progress.append(
            info(
                f"{len(links)} symlink(s) stored as links, not copies",
                f"{len(escaping)} of them point outside {src.name}",
            )
        )

    if dry_run:
        result: dict = {
            "success": True,
            "op": "fs_archive",
            "action": "create",
            "archive": str(arc),
            "source": str(src),
            "format": format_,
            "would_include": len(items),
            "symlinks_archived": len(links),
            "symlinks_pointing_outside": [str(p.relative_to(src)) for p in escaping[:10]],
            "dry_run": True,
            "progress": progress,
        }
        if links:
            result["hint"] = _links_hint(links, escaping, src)
        result["token_estimate"] = len(str(result)) // 4
        return result

    # zipfile/tarfile open the destination in "w", which truncates whatever is
    # already there. With no check, `path` pointing at an ordinary file silently
    # replaced it with a zip -- and `path` is the easy one to get wrong, since
    # the file being archived goes in `target`. A sweep destroyed a text file
    # exactly this way. Refuse a non-archive destination, and keep a snapshot
    # when replacing a real archive.
    if arc.exists():
        if arc.is_dir():
            return _error(
                "fs_archive",
                f"Destination is a directory: {arc}",
                "Pass the archive file path in 'path' and the file or folder to archive in 'target'.",
            )
        if not str(arc).lower().endswith(_ARCHIVE_SUFFIXES):
            return _error(
                "fs_archive",
                f"Refusing to overwrite non-archive file: {arc.name}",
                "'path' is the archive to write and 'target' is what goes into it -- they look "
                f"swapped here. To archive {arc.name}, pass it as 'target' and give 'path' a "
                f"name ending in .zip or .tar.gz.",
            )
        backup = snapshot(str(arc))
        progress.append(
            info(f"Replacing existing archive {arc.name}", f"snapshot {Path(backup).name}")
        )

    arc.parent.mkdir(parents=True, exist_ok=True)

    if format_ == "zip":
        with zipfile.ZipFile(arc, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if src.is_dir():
                for item in items:
                    zf.write(item, item.relative_to(src.parent))
                for link in links:
                    _zip_symlink(zf, link, link.relative_to(src.parent))
            else:
                zf.write(src, src.name)
    else:  # tar.gz
        with tarfile.open(arc, "w:gz") as tf:
            tf.add(str(src), arcname=src.name)

    arc_size_kb = size_kb(arc.stat().st_size)
    progress.append(ok(f"Created {arc.name}", f"{arc_size_kb} KB"))

    result = {
        "success": True,
        "op": "fs_archive",
        "action": "create",
        "archive": str(arc),
        "source": str(src),
        "format": format_,
        "files_archived": len(items),
        "symlinks_archived": len(links),
        "symlinks_pointing_outside": [str(p.relative_to(src)) for p in escaping[:10]],
        "size_kb": arc_size_kb,
        "progress": progress,
    }
    if links:
        result["hint"] = _links_hint(links, escaping, src)
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
            "Extract to an empty or different target directory, or remove the "
            "conflicting files first — fs_archive never overwrites existing files.",
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
    # zipfile.extractall() writes a symlink entry out as a plain file holding the
    # target path, so a zip that stores links would not round-trip the way the
    # tar.gz side does. Recreate them, minus any that would resolve outside the
    # destination -- the same rule tarfile's filter="data" applies below.
    skipped_links: list[str] = []
    with zipfile.ZipFile(arc, "r") as zf:
        infos = zf.infolist()
        link_infos = [zi for zi in infos if _is_zip_symlink(zi)]
        zf.extractall(out_dir, members=[zi for zi in infos if not _is_zip_symlink(zi)])
        for zi in link_infos:
            dest = out_dir / zi.filename
            target = zf.read(zi).decode("utf-8", "replace")
            if (
                not Path(os.path.join(dest.parent, target))
                .resolve()
                .is_relative_to(out_dir.resolve())
            ):
                skipped_links.append(zi.filename)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, dest)

    # "Extracted 3 files" counted the directory entries in the archive too, so
    # a two-file archive under one folder reported three files -- the same
    # number the info line above correctly calls "entries".
    dirs = sum(1 for n in names if n.endswith("/"))
    syms = len(link_infos)
    files = len(names) - dirs - syms
    progress.append(
        ok(
            f"Extracted {len(names) - len(skipped_links)} entries to {out_dir.name}",
            f"{files} file(s), {dirs} dir(s), {syms - len(skipped_links)} symlink(s)",
        )
    )
    result = {
        "success": True,
        "op": "fs_archive",
        "action": "extract",
        "archive": str(arc),
        "target": str(out_dir),
        "extracted": len(names) - len(skipped_links),
        "extracted_files": files,
        "extracted_dirs": dirs,
        "extracted_symlinks": syms - len(skipped_links),
        "progress": progress,
    }
    if skipped_links:
        progress.append(
            warn(
                f"Skipped {len(skipped_links)} symlink(s) pointing outside {out_dir.name}",
                ", ".join(skipped_links[:5]),
            )
        )
        result["symlinks_skipped"] = skipped_links[:10]
        result["hint"] = (
            f"{len(skipped_links)} symlink(s) in the archive resolve outside {out_dir.name} and "
            "were not recreated, so nothing lands outside the directory you extracted into. "
            "Extract somewhere that already holds their targets if you need them to resolve."
        )
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
            "Extract to an empty or different target directory, or remove the "
            "conflicting files first — fs_archive never overwrites existing files.",
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
    skipped_links: list[str] = []

    def _keep(member: tarfile.TarInfo, dest: str) -> tarfile.TarInfo | None:
        """Drop a link that escapes the destination; keep the rest.

        filter="data" *raises* on one, so a single link to an absolute path --
        which is an ordinary thing to find in a backup -- cost the caller the
        whole archive and left nothing extracted. The zip side skips just that
        entry, so this does too.
        """
        try:
            return tarfile.data_filter(member, dest)
        except tarfile.AbsoluteLinkError, tarfile.LinkOutsideDestinationError:
            skipped_links.append(member.name)
            return None

    with tarfile.open(arc, "r:gz") as tf:
        tf.extractall(out_dir, filter=_keep)
        # Same count, same correction as the zip side: a tar lists its
        # directories as members, and they are not files.
        dirs = sum(1 for m in tf.getmembers() if m.isdir())
        syms = sum(1 for m in tf.getmembers() if m.issym())
    files = len(members) - dirs - syms
    extracted = len(members) - len(skipped_links)
    progress.append(
        ok(
            f"Extracted {extracted} entries to {out_dir.name}",
            f"{files} file(s), {dirs} dir(s), {syms - len(skipped_links)} symlink(s)",
        )
    )
    result = {
        "success": True,
        "op": "fs_archive",
        "action": "extract",
        "archive": str(arc),
        "target": str(out_dir),
        "extracted": extracted,
        "extracted_files": files,
        "extracted_dirs": dirs,
        "extracted_symlinks": syms - len(skipped_links),
        "progress": progress,
    }
    if skipped_links:
        progress.append(
            warn(
                f"Skipped {len(skipped_links)} symlink(s) pointing outside {out_dir.name}",
                ", ".join(skipped_links[:5]),
            )
        )
        result["symlinks_skipped"] = skipped_links[:10]
        result["hint"] = (
            f"{len(skipped_links)} symlink(s) in the archive resolve outside {out_dir.name} and "
            "were not recreated, so nothing lands outside the directory you extracted into. "
            "Extract somewhere that already holds their targets if you need them to resolve."
        )
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
