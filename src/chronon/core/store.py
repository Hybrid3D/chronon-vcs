from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import (
    AlreadyInitialized,
    FileError,
    InvalidArgument,
    NotImplementedMode,
    RepositoryNotFound,
    ResourceNotTracked,
)
from .text import encode_working_text, read_working_text


def now_iso() -> str:
    """Current UTC time as a ``...Z`` ISO 8601 string (sorts lexicographically)."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_resource_id() -> str:
    """Mint an opaque, stable identity for a tracked resource.

    The id is 128 random bits rendered as hex. It is assigned once, when a file
    starts being tracked, and then follows the file across renames (`chronon
    mv`) so history can be attributed to the file rather than to its path.
    """
    return secrets.token_hex(16)


CONFIG_TEXT = """format_version = 1
mode = "manual"

[auto]
idle_seconds = 900
max_interval_seconds = 7200
"""


def content_hash(content: str | bytes) -> str:
    data = encode_working_text(content) if isinstance(content, str) else content
    return "sha256:" + hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        mode = (path.stat().st_mode & 0o777) if path.exists() else 0o644
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encode_working_text(data))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def discover_root(start: str | Path | None = None) -> Path:
    current = Path(start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".chronon" / "config.toml").is_file():
            return candidate
    raise RepositoryNotFound(
        "not inside a chronon repository",
        path=str(current),
        hint="name the vault to use (see list_vaults / 'chronon list-vaults'), "
        "or initialize this directory first",
    )


def resolve_root(vault: str | None = None, start: str | Path | None = None) -> Path:
    """Resolve a repository root either by registered vault name or by
    searching upward from `start` (default: cwd), git-style.

    `vault` is a name registered via `chronon add-vault` (see core/vaults.py). It
    is looked up in the global, per-user registry — not inside any repository —
    so it works from any current directory. Omitting it preserves the original
    behavior: search upward from `start`/cwd for the nearest `.chronon/`, falling
    back to a `.chronon-workspace` pin (see `chronon set-vault`) above `start`
    when no vault is found there directly.
    """
    if vault is not None:
        from .vaults import resolve_vault

        return resolve_vault(vault)
    try:
        return discover_root(start)
    except RepositoryNotFound:
        from .vaults import read_workspace_vault, resolve_vault

        pinned = read_workspace_vault(start)
        if pinned is None:
            raise
        return resolve_vault(pinned)


def _ensure_gitignore(root: Path) -> None:
    ignore = root / ".gitignore"
    marker = "/.chronon/"
    existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    if marker in {line.strip() for line in existing.splitlines()}:
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    atomic_write(ignore, existing + prefix + marker + "\n")


def init_store(directory: str | Path = ".", mode: str = "manual") -> dict[str, Any]:
    if mode != "manual":
        if mode == "auto":
            raise NotImplementedMode(
                "auto commit mode is planned but not implemented",
                mode=mode,
                hint="initialize in manual mode (the default)",
            )
        raise InvalidArgument("mode must be 'manual' or 'auto'", mode=mode)

    root = Path(directory).expanduser().resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FileError(
            "cannot create initialization directory", path=str(root)
        ) from exc
    if not root.is_dir():
        raise FileError("initialization target is not a directory", path=str(root))

    metadata = root / ".chronon"
    if metadata.exists() and not metadata.is_dir():
        raise FileError(".chronon exists but is not a directory", path=str(metadata))
    config_path = metadata / "config.toml"
    created = not config_path.exists()
    if config_path.exists():
        try:
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise AlreadyInitialized(
                "existing chronon configuration is invalid", path=str(config_path)
            ) from exc
        format_version = config.get("format_version")
        if (
            not isinstance(format_version, int)
            or isinstance(format_version, bool)
            or format_version != 1
            or config.get("mode") != mode
        ):
            raise AlreadyInitialized(
                "repository already exists with different settings",
                path=str(root),
                current_mode=config.get("mode"),
                requested_mode=mode,
            )
    else:
        try:
            metadata.mkdir(parents=True, exist_ok=True)
            atomic_write(config_path, CONFIG_TEXT)
        except OSError as exc:
            raise FileError(
                "cannot create chronon metadata", path=str(metadata)
            ) from exc
    try:
        (metadata / "resources").mkdir(exist_ok=True)
        (metadata / "schemas").mkdir(exist_ok=True)
        _ensure_gitignore(root)
    except OSError as exc:
        raise FileError(
            "cannot finish repository initialization", path=str(root)
        ) from exc
    return {"root": str(root), "mode": "manual", "created": created}


class Store:
    def __init__(
        self, root: str | Path | None = None, vault: str | None = None
    ) -> None:
        self.root = resolve_root(vault, root)
        self.metadata = self.root / ".chronon"
        self._id_cache: dict[str, str] = {}
        try:
            self.config = tomllib.loads(
                (self.metadata / "config.toml").read_text(encoding="utf-8")
            )
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise RepositoryNotFound(
                "cannot read chronon configuration", path=str(self.root)
            ) from exc
        format_version = self.config.get("format_version")
        if (
            not isinstance(format_version, int)
            or isinstance(format_version, bool)
            or format_version != 1
        ):
            raise RepositoryNotFound(
                "unsupported chronon repository format",
                path=str(self.root),
                format_version=format_version,
            )
        if self.config.get("mode") != "manual":
            raise RepositoryNotFound(
                "unsupported chronon repository mode",
                path=str(self.root),
                mode=self.config.get("mode"),
            )

    def normalize_resource(self, resource: str | Path) -> str:
        supplied = Path(resource).expanduser()
        absolute = (
            supplied.resolve()
            if supplied.is_absolute()
            else (self.root / supplied).resolve()
        )
        try:
            relative = absolute.relative_to(self.root)
        except ValueError as exc:
            raise FileError(
                "resource must be inside repository root", path=str(absolute)
            ) from exc
        if not relative.parts or relative.parts[0] == ".chronon":
            raise FileError(".chronon metadata cannot be tracked", path=str(relative))
        return relative.as_posix()

    def normalize_directory(self, directory: str | Path) -> str:
        """Return a repository-relative directory without consulting its contents."""
        supplied = Path(directory).expanduser()
        absolute = (
            supplied.resolve()
            if supplied.is_absolute()
            else (self.root / supplied).resolve()
        )
        try:
            relative = absolute.relative_to(self.root)
        except ValueError as exc:
            raise FileError(
                "directory must be inside repository root", path=str(absolute)
            ) from exc
        if relative.parts and relative.parts[0] == ".chronon":
            raise FileError(".chronon metadata cannot be listed", path=str(relative))
        return "." if not relative.parts else relative.as_posix()

    def working_path(self, resource: str | Path) -> Path:
        return self.root / self.normalize_resource(resource)

    def resource_dir(self, resource: str | Path) -> Path:
        relative = self.normalize_resource(resource)
        return self.metadata / "resources" / Path(relative)

    def descriptor_path(self, resource: str | Path) -> Path:
        return self.resource_dir(resource) / "resource.json"

    def is_tracked(self, resource: str | Path) -> bool:
        return self.descriptor_path(resource).is_file()

    def read_descriptor(self, resource: str | Path) -> dict[str, Any]:
        path = self.descriptor_path(resource)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ResourceNotTracked(
                "resource is not tracked", resource=str(resource)
            ) from exc
        except (OSError, ValueError) as exc:
            raise FileError(
                "cannot read resource descriptor", resource=str(resource)
            ) from exc
        if not isinstance(data, dict):
            raise FileError("resource descriptor is malformed", resource=str(resource))
        return data

    def ensure_resource_id(self, resource: str | Path) -> str:
        """Return the resource's id, minting and persisting one if absent.

        Repositories created before ids existed have descriptors without an
        ``id`` field; this backfills them transparently on first use.
        """
        relative = self.normalize_resource(resource)
        cached = self._id_cache.get(relative)
        if cached:
            return cached
        data = self.read_descriptor(relative)
        resource_id = data.get("id")
        if not resource_id or not isinstance(resource_id, str):
            resource_id = new_resource_id()
            data["id"] = resource_id
            data.setdefault("resource", relative)
            atomic_write_json(self.descriptor_path(relative), data)
        self._id_cache[relative] = resource_id
        return resource_id

    def forget_id_cache(self, resource: str | Path) -> None:
        self._id_cache.pop(self.normalize_resource(resource), None)

    def require_tracked(self, resource: str | Path) -> str:
        relative = self.normalize_resource(resource)
        if not self.is_tracked(relative):
            raise ResourceNotTracked(
                "resource is not tracked",
                resource=relative,
                hint="create a new file with create_resource, or track a file that "
                "already exists on disk with add_resource",
            )
        self.ensure_resource_id(relative)
        return relative

    def add(self, resource: str | Path) -> dict[str, Any]:
        relative = self.normalize_resource(resource)
        working = self.root / relative
        if not working.is_file():
            raise FileError("resource file does not exist", resource=relative)
        try:
            content = read_working_text(working)
        except OSError as exc:
            raise FileError("resource file is not readable", resource=relative) from exc
        resource_dir = self.resource_dir(relative)
        created = not self.is_tracked(relative)
        resource_dir.mkdir(parents=True, exist_ok=True)
        if created:
            atomic_write_json(
                resource_dir / "resource.json",
                {
                    "resource": relative,
                    "id": new_resource_id(),
                    "path_log": [{"path": relative, "since": now_iso()}],
                },
            )
            atomic_write_json(
                resource_dir / "state.json",
                {
                    "last_written_hash": content_hash(content),
                    "last_written_at": None,
                    "last_seq": 0,
                    "working_generation": 0,
                },
            )
        return {
            "resource": relative,
            "tracked": True,
            "created": created,
            "state": "untracked",
        }

    def tracked_resources(self) -> list[str]:
        base = self.metadata / "resources"
        resources: list[str] = []
        for descriptor in base.rglob("resource.json"):
            try:
                value = json.loads(descriptor.read_text(encoding="utf-8"))
                if not isinstance(value, dict) or not isinstance(
                    value.get("resource"), str
                ):
                    raise ValueError("missing string resource field")
                resource = self.normalize_resource(value["resource"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                raise FileError(
                    "resource descriptor is corrupt", path=str(descriptor)
                ) from exc
            expected = descriptor.parent.relative_to(base).as_posix()
            if resource != expected:
                raise FileError(
                    "resource descriptor path does not match its resource",
                    path=str(descriptor),
                    resource=resource,
                    expected=expected,
                )
            resources.append(resource)
        return sorted(set(resources))
