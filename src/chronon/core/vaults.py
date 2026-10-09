"""Global (per-user) registry mapping vault names to chronon repository roots.

A "vault" is nothing more than a name for a directory that has been (or will be)
``chronon init``-ed. It lets a human or an agent operate on a repository from any
current working directory without ``cd``-ing into it first, similar in spirit to
how tools like Obsidian name a vault by location rather than by path.

The registry itself lives outside any single repository, at
``$CHRONON_CONFIG_HOME/vaults.toml`` (default ``~/.config/chronon/vaults.toml``),
because it has to be readable before we know which repository we are talking
about. Nothing here mutates repository state; it only resolves a name to a path.

A second, unrelated file lives *inside* a plain workspace directory (one that is
not itself a vault): ``.chronon-workspace``, which pins that directory tree to
one registered vault name so ``chronon`` commands there resolve it without a
``--vault`` flag, the same way ``.chronon/config.toml`` lets commands run
without a flag from inside the vault itself. `chronon set-vault` writes it;
`resolve_root` in `store.py` reads it as a fallback once directory discovery
of an actual vault fails.

A third, unrelated mechanism selects a vault inline in a resource reference
itself: `chronon://<vault>/<path>`, parsed by `split_vault_uri` below. Unlike
the previous two, this is not a file — it lets one string carry both the vault
and the path, so pointing another agent (or a document, or a chat message) at
one managed file does not require separately naming `--vault`/`vault=`.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .errors import FileError, InvalidArgument
from .lock import exclusive_file_lock

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

WORKSPACE_FILE = ".chronon-workspace"
URI_SCHEME = "chronon"
URI_PREFIX = f"{URI_SCHEME}://"


def config_home() -> Path:
    override = os.environ.get("CHRONON_CONFIG_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        windows_home = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if windows_home:
            return Path(windows_home) / "chronon"
        return Path.home() / "AppData" / "Roaming" / "chronon"
    xdg_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_home:
        return Path(xdg_home).expanduser() / "chronon"
    return Path.home() / ".config" / "chronon"


def registry_path() -> Path:
    return config_home() / "vaults.toml"


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _load() -> dict[str, str]:
    path = registry_path()
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise FileError("vault registry is invalid", path=str(path)) from exc
    vaults = data.get("vaults", {})
    if not isinstance(vaults, dict):
        raise FileError("vault registry is invalid", path=str(path))
    if not all(
        isinstance(name, str) and isinstance(value, str)
        for name, value in vaults.items()
    ):
        raise FileError("vault registry is invalid", path=str(path))
    return dict(vaults)


def _save(vaults: dict[str, str]) -> None:
    lines = [
        "# managed by Chronon vault registry commands — do not edit while chronon is running",
        "[vaults]",
    ]
    for name in sorted(vaults):
        lines.append(
            f"{json.dumps(name)} = {json.dumps(vaults[name], ensure_ascii=False)}"
        )
    _atomic_write(registry_path(), "\n".join(lines) + "\n")


def _validate_name(name: str) -> None:
    if not _NAME_RE.fullmatch(name):
        raise InvalidArgument(
            "vault name must start with a letter or digit and contain only "
            "letters, digits, '-', or '_'",
            name=name,
        )


def list_vaults() -> dict[str, str]:
    """Return {name: absolute_path} for every registered vault."""
    return _load()


def _repository_root(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not (resolved / ".chronon" / "config.toml").is_file():
        raise FileError(
            "path is not a chronon repository",
            path=str(resolved),
            hint="initialize that directory first, or pass the repository root",
        )
    return resolved


def add_vault(name: str, path: str | Path) -> dict[str, Any]:
    """Register a new name, or leave an identical registration unchanged."""
    _validate_name(name)
    resolved = _repository_root(path)
    with exclusive_file_lock(config_home() / "vaults.lock"):
        vaults = _load()
        created = name not in vaults
        if not created and vaults[name] != str(resolved):
            raise InvalidArgument(
                "vault name is already registered to a different path",
                name=name,
                hint="ask the human administrator to run "
                f"'chronon admin set-vault-path {name} PATH' outside the agent session",
            )
        if created:
            vaults[name] = str(resolved)
            _save(vaults)
    return {"name": name, "path": str(resolved), "created": created}


def set_vault_path(name: str, path: str | Path) -> dict[str, Any]:
    """Repoint an existing registration without moving files or requiring the old root."""
    _validate_name(name)
    with exclusive_file_lock(config_home() / "vaults.lock"):
        vaults = _load()
        if name not in vaults:
            raise InvalidArgument(
                "no such vault", name=name, hint="call list_vaults to see valid names"
            )
        resolved = _repository_root(path)
        previous_path = vaults[name]
        updated = previous_path != str(resolved)
        if updated:
            vaults[name] = str(resolved)
            _save(vaults)
    return {
        "name": name,
        "path": str(resolved),
        "previous_path": previous_path,
        "updated": updated,
    }


def remove_vault(name: str) -> dict[str, Any]:
    with exclusive_file_lock(config_home() / "vaults.lock"):
        vaults = _load()
        if name not in vaults:
            raise InvalidArgument(
                "no such vault", name=name, hint="call list_vaults to see valid names"
            )
        del vaults[name]
        _save(vaults)
    return {"name": name, "removed": True}


def resolve_vault(name: str) -> Path:
    vaults = _load()
    if name not in vaults:
        raise InvalidArgument(
            "no such vault", name=name, hint="call list_vaults to see valid names"
        )
    path = Path(vaults[name])
    if not (path / ".chronon" / "config.toml").is_file():
        raise FileError(
            "vault is registered but no longer a chronon repository",
            name=name,
            path=str(path),
            hint="ask the human administrator to repair or remove this vault",
        )
    return path


def _find_workspace_file(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        marker = candidate / WORKSPACE_FILE
        if marker.is_file():
            return marker
    return None


def read_workspace_vault(start: str | Path | None = None) -> str | None:
    """Return the vault name pinned above `start` (default: cwd), if any.

    Walks upward from `start` looking for `.chronon-workspace`, the same way
    `discover_root` walks upward looking for `.chronon/`. Returns None rather
    than raising when no pin exists, so callers can fall back further.
    """
    current = Path(start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    path = _find_workspace_file(current)
    if path is None:
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise FileError("workspace vault pin is invalid", path=str(path)) from exc
    vault = data.get("vault")
    if not isinstance(vault, str) or not vault:
        raise FileError(
            "workspace vault pin is invalid",
            path=str(path),
            hint="expected a 'vault = \"<name>\"' entry",
        )
    return vault


def set_vault(name: str, directory: str | Path = ".") -> dict[str, Any]:
    """Pin `directory`'s tree to registered vault `name` via `.chronon-workspace`.

    Once pinned, `chronon` commands run anywhere under `directory` without
    `--vault` resolve to `name` — see `resolve_root` in `store.py`. Requires
    `name` to already be registered, so the pin can never point at nothing.
    """
    _validate_name(name)
    resolve_vault(name)
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise InvalidArgument(
            "workspace destination must be an existing directory", path=str(root)
        )
    path = root / WORKSPACE_FILE
    created = not path.exists()
    _atomic_write(
        path,
        "# managed by 'chronon set-vault' — do not edit while chronon is running\n"
        f"vault = {json.dumps(name)}\n",
    )
    return {"path": str(path), "vault": name, "created": created}


def unset_vault(directory: str | Path = ".") -> dict[str, Any]:
    """Remove `directory`'s `.chronon-workspace` pin, if one exists there."""
    root = Path(directory).expanduser().resolve()
    path = root / WORKSPACE_FILE
    if not path.is_file():
        raise InvalidArgument("no workspace vault pin here", path=str(path))
    try:
        path.unlink()
    except OSError as exc:
        raise FileError("cannot remove workspace vault pin", path=str(path)) from exc
    return {"path": str(path), "removed": True}


def split_vault_uri(resource: str, vault: str | None = None) -> tuple[str | None, str]:
    """Split a `chronon://<vault>/<path>` reference into `(vault, path)`.

    A resource that does not start with `chronon://` passes through unchanged,
    paired with `vault` as given. This lets a single string carry its own
    vault selector — handy for pointing at one managed file from outside any
    chronon-aware context ("read chronon://notes/apartment.md") without
    separately naming `--vault`/`vault=`. Conflicts with an explicitly passed
    `vault` raise rather than silently picking one.
    """
    if not resource.startswith(URI_PREFIX):
        return vault, resource
    parsed = urlsplit(resource)
    if parsed.query or parsed.fragment:
        raise InvalidArgument(
            "chronon:// URI must not include a query or fragment", uri=resource
        )
    path = unquote(parsed.path).lstrip("/")
    if not path:
        raise InvalidArgument(
            "chronon:// URI must include a resource path",
            uri=resource,
            hint="expected chronon://<vault>/<path>",
        )
    uri_vault = unquote(parsed.netloc) or None
    if uri_vault is not None:
        _validate_name(uri_vault)
        if vault is not None and vault != uri_vault:
            raise InvalidArgument(
                "chronon:// URI vault conflicts with an explicitly given vault",
                uri_vault=uri_vault,
                vault=vault,
            )
    return uri_vault or vault, path
