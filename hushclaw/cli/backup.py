"""Backup/export helpers for migrating local HushClaw state between devices."""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path


ARCHIVE_FORMAT = "hushclaw-backup"
ARCHIVE_VERSION = 1
MANIFEST_NAME = "manifest.json"
ARCHIVE_CONFIG = Path("config") / "hushclaw.toml"
ARCHIVE_PLUGIN_DIR = Path("config") / "tools"
ARCHIVE_DATA_DIR = Path("data")
DB_NAME = "memory.db"
DB_SIDE_CARS = {DB_NAME, f"{DB_NAME}-wal", f"{DB_NAME}-shm"}


def _copy_path(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _backup_sqlite_db(src_db: Path, dst_db: Path) -> None:
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(src_db)
    try:
        dst_conn = sqlite3.connect(dst_db)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _stage_data_dir(src_data_dir: Path, dst_data_dir: Path) -> None:
    if not src_data_dir.exists():
        return
    dst_data_dir.mkdir(parents=True, exist_ok=True)
    for child in src_data_dir.iterdir():
        if child.name in DB_SIDE_CARS:
            continue
        _copy_path(child, dst_data_dir / child.name)
    src_db = src_data_dir / DB_NAME
    if src_db.exists():
        _backup_sqlite_db(src_db, dst_data_dir / DB_NAME)


def _write_manifest(root: Path, *, config_file: Path, data_dir: Path, plugin_dir: Path | None) -> None:
    manifest = {
        "format": ARCHIVE_FORMAT,
        "version": ARCHIVE_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "config_file": str(config_file),
            "data_dir": str(data_dir),
            "plugin_dir": str(plugin_dir) if plugin_dir else "",
        },
        "contents": {
            "config": (root / ARCHIVE_CONFIG).exists(),
            "plugins": (root / ARCHIVE_PLUGIN_DIR).exists(),
            "data": (root / ARCHIVE_DATA_DIR).exists(),
        },
    }
    (root / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def create_backup_archive(
    archive_path: Path,
    *,
    config_file: Path,
    data_dir: Path,
    plugin_dir: Path | None = None,
    include_config: bool = True,
    include_plugins: bool = True,
) -> Path:
    archive_path = archive_path.expanduser().resolve()
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="hushclaw-backup-") as tmp:
        staging = Path(tmp)
        if include_config and config_file.exists():
            _copy_path(config_file, staging / ARCHIVE_CONFIG)
        if include_plugins and plugin_dir and plugin_dir.exists():
            _copy_path(plugin_dir, staging / ARCHIVE_PLUGIN_DIR)
        _stage_data_dir(data_dir, staging / ARCHIVE_DATA_DIR)
        _write_manifest(staging, config_file=config_file, data_dir=data_dir, plugin_dir=plugin_dir)

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(staging.rglob("*")):
                if path.is_dir():
                    continue
                zf.write(path, path.relative_to(staging).as_posix())
    return archive_path


def _load_manifest_from_dir(root: Path) -> dict:
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.exists():
        raise ValueError("Backup archive is missing manifest.json.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != ARCHIVE_FORMAT:
        raise ValueError("Unsupported backup archive format.")
    version = int(manifest.get("version", 0) or 0)
    if version > ARCHIVE_VERSION:
        raise ValueError(
            f"Backup archive version {version} is newer than this client supports ({ARCHIVE_VERSION})."
        )
    return manifest


def _safe_extract_zip(archive_path: Path, target_dir: Path) -> None:
    target_root = target_dir.resolve()
    with zipfile.ZipFile(archive_path) as zf:
        for info in zf.infolist():
            rel = Path(info.filename)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"Unsafe archive entry: {info.filename}")
            dst = (target_root / rel).resolve()
            if dst != target_root and target_root not in dst.parents:
                raise ValueError(f"Unsafe archive entry: {info.filename}")
            if info.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)


def _archive_embedded_data_dir(archive_path: Path) -> Path | None:
    try:
        with zipfile.ZipFile(archive_path) as zf:
            try:
                raw = zf.read(ARCHIVE_CONFIG.as_posix())
            except KeyError:
                return None
    except zipfile.BadZipFile as e:
        raise ValueError(f"Invalid backup archive: {archive_path}") from e

    try:
        config_data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    data_dir = config_data.get("memory", {}).get("data_dir")
    if not data_dir:
        return None
    return Path(str(data_dir)).expanduser()


def restore_backup_archive(
    archive_path: Path,
    *,
    config_file: Path,
    data_dir: Path,
    plugin_dir: Path | None = None,
    restore_config: bool = True,
    restore_plugins: bool = True,
    force: bool = False,
) -> dict:
    archive_path = archive_path.expanduser().resolve()
    if not archive_path.exists():
        raise FileNotFoundError(f"Backup archive not found: {archive_path}")

    with tempfile.TemporaryDirectory(prefix="hushclaw-restore-") as tmp:
        extracted = Path(tmp)
        _safe_extract_zip(archive_path, extracted)
        manifest = _load_manifest_from_dir(extracted)

        src_config = extracted / ARCHIVE_CONFIG
        src_plugins = extracted / ARCHIVE_PLUGIN_DIR
        src_data = extracted / ARCHIVE_DATA_DIR

        config_file = config_file.expanduser()
        data_dir = data_dir.expanduser()
        plugin_dir = plugin_dir.expanduser() if plugin_dir else None

        if restore_config and src_config.exists() and config_file.exists() and not force:
            raise FileExistsError(f"Refusing to overwrite existing config file: {config_file}")
        if restore_plugins and plugin_dir and src_plugins.exists() and plugin_dir.exists() and any(plugin_dir.iterdir()) and not force:
            raise FileExistsError(f"Refusing to overwrite existing plugin directory: {plugin_dir}")
        if src_data.exists() and data_dir.exists() and any(data_dir.iterdir()) and not force:
            raise FileExistsError(f"Refusing to overwrite existing data directory: {data_dir}")

        if restore_config and src_config.exists():
            config_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_config, config_file)

        if restore_plugins and plugin_dir and src_plugins.exists():
            if force and plugin_dir.exists():
                shutil.rmtree(plugin_dir)
            plugin_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_plugins, plugin_dir, dirs_exist_ok=False)

        if src_data.exists():
            if force and data_dir.exists():
                shutil.rmtree(data_dir)
            data_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_data, data_dir, dirs_exist_ok=False)

        return manifest


def _default_backup_filename() -> str:
    return time.strftime("hushclaw-backup-%Y%m%d-%H%M%S.zip", time.localtime())


def cmd_backup_export(args) -> int:
    from hushclaw.config.loader import get_config_dir, load_config

    config = load_config()
    config_dir = get_config_dir()
    config_file = config_dir / "hushclaw.toml"
    plugin_dir = config_dir / "tools"
    output = Path(args.output).expanduser() if args.output else Path.cwd() / _default_backup_filename()

    create_backup_archive(
        output,
        config_file=config_file,
        data_dir=config.memory.data_dir,
        plugin_dir=plugin_dir,
        include_config=not getattr(args, "skip_config", False),
        include_plugins=not getattr(args, "skip_plugins", False),
    )
    print(f"Created backup: {output}")
    print(f"  config: {config_file}")
    print(f"  data:   {config.memory.data_dir}")
    if not getattr(args, "skip_plugins", False):
        print(f"  tools:  {plugin_dir}")
    return 0


def cmd_backup_import(args) -> int:
    from hushclaw.config.loader import get_config_dir, get_data_dir

    archive_path = Path(args.archive).expanduser()
    config_dir = get_config_dir()
    config_file = Path(args.config_file).expanduser() if getattr(args, "config_file", None) else config_dir / "hushclaw.toml"
    plugin_dir = Path(args.plugin_dir).expanduser() if getattr(args, "plugin_dir", None) else config_dir / "tools"

    if getattr(args, "data_dir", None):
        data_dir = Path(args.data_dir).expanduser()
    else:
        data_dir = _archive_embedded_data_dir(archive_path) or get_data_dir()

    manifest = restore_backup_archive(
        archive_path,
        config_file=config_file,
        data_dir=data_dir,
        plugin_dir=plugin_dir,
        restore_config=not getattr(args, "skip_config", False),
        restore_plugins=not getattr(args, "skip_plugins", False),
        force=bool(getattr(args, "force", False)),
    )
    print(f"Restored backup: {archive_path}")
    print(f"  config: {config_file}")
    print(f"  data:   {data_dir}")
    if not getattr(args, "skip_plugins", False):
        print(f"  tools:  {plugin_dir}")
    print(f"  created: {manifest.get('created_at', 'unknown')}")
    return 0
