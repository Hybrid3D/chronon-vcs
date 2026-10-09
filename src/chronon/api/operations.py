from __future__ import annotations

import getpass
import json
import os
import shutil
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from chronon.core.diff import compare_content
from chronon.core.errors import (
    FileError,
    ForeignChange,
    InvalidArgument,
    InvalidRevspec,
    NothingToCommit,
    PathError,
    PreconditionRequired,
    ResourceAlreadyTracked,
    RevisionConflict,
    ValidationFailed,
)
from chronon.core.lock import resource_operation_lock
from chronon.core.path import get_value
from chronon.core.path import set_value as set_document_value
from chronon.core.path import unset_value as unset_document_value
from chronon.core.revspec import parse_time_filter, resolve_revision
from chronon.core.snapshot import (
    Commit,
    create_snapshot,
    read_index,
    read_snapshot,
)
from chronon.core.state import resource_state, update_state
from chronon.core.store import (
    Store,
    atomic_write,
    atomic_write_json,
    content_hash,
    init_store,
    new_resource_id,
    now_iso,
)
from chronon.core.text import has_lone_surrogates, read_working_text
from chronon.harness.base import parse_content, validate_content

MISSING = object()


def init_repository(
    directory: str | Path = ".",
    mode: str = "manual",
    register_vault: str | None = None,
) -> dict[str, Any]:
    result = init_store(directory, mode)
    if register_vault:
        from chronon.core.vaults import add_vault

        result["vault"] = add_vault(register_vault, result["root"])
    return result


def list_vaults() -> dict[str, Any]:
    """List registered vault names without exposing their storage paths."""
    from chronon.core.vaults import list_vaults as _list_vaults

    return {"vaults": [{"name": name} for name in sorted(_list_vaults())]}


def admin_vault_path(name: str) -> dict[str, str]:
    """Return a registered path for human administration, even if it is offline.

    This is a CLI-only administration operation, not an authorization boundary.
    """
    from chronon.core.vaults import list_vaults as _list_vaults

    vaults = _list_vaults()
    if name not in vaults:
        raise InvalidArgument(
            "no such vault", name=name, hint="call list_vaults to see valid names"
        )
    return {"name": name, "path": vaults[name]}


def admin_set_vault_path(name: str, path: str | Path) -> dict[str, Any]:
    """Change an existing registration for human administration, without moving files.

    This is a CLI-only administration operation, not an authorization boundary.
    """
    from chronon.core.vaults import set_vault_path

    return set_vault_path(name, path)


def add_vault(name: str, path: str | Path) -> dict[str, Any]:
    from chronon.core.vaults import add_vault as _add_vault

    return _add_vault(name, path)


def remove_vault(name: str) -> dict[str, Any]:
    from chronon.core.vaults import remove_vault as _remove_vault

    return _remove_vault(name)


def set_vault(name: str, directory: str | Path = ".") -> dict[str, Any]:
    from chronon.core.vaults import set_vault as _set_vault

    return _set_vault(name, directory)


def unset_vault(directory: str | Path = ".") -> dict[str, Any]:
    from chronon.core.vaults import unset_vault as _unset_vault

    return _unset_vault(directory)


def write_agent_instructions(
    directory: str | Path = ".",
    vault: str | None = None,
    filename: str = "CHRONON.md",
    link: bool = True,
    link_targets: Sequence[str] | None = None,
    permissions: bool = False,
) -> dict[str, Any]:
    """Write AI guidance outside a vault, optionally specialized for one vault.

    Unless ``link`` is false, the guide is also referenced from the instruction
    files the AI clients in this workspace load on their own, so no manual
    wiring step is left for the user. ``link_targets`` overrides the detected
    files with an explicit list. ``permissions`` additionally allowlists the
    recoverable Chronon commands in Claude Code's local settings.
    """
    from chronon.core.docs import (
        detect_link_targets,
        ensure_agent_link,
        ensure_agents_md,
    )
    from chronon.core.vaults import resolve_vault

    if vault:
        resolve_vault(vault)
    result = ensure_agents_md(Path(directory), filename, vault=vault)
    if not link:
        return result
    targets = (
        list(link_targets)
        if link_targets is not None
        else detect_link_targets(Path(directory), exclude=filename)
    )
    result["links"] = [
        ensure_agent_link(Path(directory), target, filename, vault=vault)
        for target in targets
    ]
    if permissions:
        from chronon.core.permissions import ensure_permissions

        result["permissions"] = ensure_permissions(Path(directory), vault=vault)
    return result


def check_agent_instructions(
    directory: str | Path = ".",
    vault: str | None = None,
    filename: str = "CHRONON.md",
    link: bool = True,
    link_targets: Sequence[str] | None = None,
    permissions: bool = False,
) -> dict[str, Any]:
    """Report whether the generated guidance and its pointers are up to date.

    Writes nothing. ``current`` is false when re-running the generator would
    change anything, which is the signal after a Chronon upgrade or when someone
    has edited a generated block by hand.
    """
    from chronon.core.docs import (
        check_agent_link,
        check_agents_md,
        detect_link_targets,
    )
    from chronon.core.vaults import resolve_vault

    if vault:
        resolve_vault(vault)
    result = check_agents_md(Path(directory), filename, vault=vault)
    result["checked"] = True
    if link:
        targets = (
            list(link_targets)
            if link_targets is not None
            else detect_link_targets(Path(directory), exclude=filename)
        )
        result["links"] = [
            check_agent_link(Path(directory), target, filename, vault=vault)
            for target in targets
        ]
    if permissions:
        from chronon.core.permissions import check_permissions

        result["permissions"] = check_permissions(Path(directory), vault=vault)
    dependents = [*result.get("links", [])]
    if "permissions" in result:
        dependents.append(result["permissions"])
    result["current"] = result["current"] and all(
        entry["current"] for entry in dependents
    )
    return result


def get_agent_instructions(
    vault: str | None = None,
    *,
    allow_scratch: bool = False,
) -> str:
    """Return the Chronon usage guide for AI agents as plain text.

    Unlike `write_agent_instructions`, this writes nothing: the text is meant
    to be returned directly by a CLI command or an MCP tool call and pasted
    into the caller's own instructions on the spot. `allow_scratch` controls
    whether the guidance documents uncommitted scratch writes as an available
    action for this agent; it defaults to off, since a one-shot commit is the
    safer default for an agent that fetches its own instructions on demand.
    """
    from chronon.core.docs import render_section, render_vault_section
    from chronon.core.vaults import resolve_vault

    if vault:
        resolve_vault(vault)
        return render_vault_section(vault, allow_scratch=allow_scratch)
    return render_section(allow_scratch=allow_scratch)


def _author(author: str | None) -> str:
    return author or os.environ.get("CHRONON_AUTHOR") or getpass.getuser() or "unknown"


def _commit_ref(ref: str, commit: Commit | None) -> dict[str, Any]:
    if commit is None:
        return {
            "ref": ref,
            "seq": None,
            "timestamp": None,
            "message": None,
            "author": None,
        }
    return {
        "ref": ref,
        "seq": commit.seq,
        "timestamp": commit.timestamp,
        "message": commit.message,
        "author": commit.author,
    }


class ChrononRepository:
    def __init__(
        self, root: str | Path | None = None, vault: str | None = None
    ) -> None:
        self._root = root
        self._vault = vault
        self._store: Store | None = None

    @property
    def store(self) -> Store:
        """The active `Store`, built lazily.

        A `chronon://` resource URI must get a chance to pick (or override) the
        vault *before* a `Store` is ever constructed — otherwise a workspace with
        no cwd-discoverable vault and no `--vault`/`vault=` would fail to resolve
        before `_select`/`_select_pair` can read the resource. Every method that
        takes a resource calls one of those first, so by the time this property
        is read, `_vault` (and thus the root this resolves to) is already final.
        """
        if self._store is None:
            self._store = Store(self._root, vault=self._vault)
        return self._store

    def _select(self, resource: str | Path) -> str:
        """Strip a `chronon://<vault>/<path>` URI's vault from `resource`.

        Switches this repository to that vault when it differs from the one
        currently in use, so a resource reference can carry its own vault inline
        instead of requiring a separate `vault=`/`--vault`. Returns the plain
        resource path either way. Callers must assign the result before reading
        `self.store` — `self.store.method(self._select(x))` would evaluate
        `self.store` first and defeat this.
        """
        from chronon.core.vaults import split_vault_uri

        text = resource if isinstance(resource, str) else str(resource)
        vault, plain = split_vault_uri(text, self._vault)
        if self._store is None or vault != self._vault:
            self._vault = vault
            self._store = Store(self._root, vault=vault)
        return plain

    def _select_pair(
        self, source: str | Path, destination: str | Path
    ) -> tuple[str, str]:
        """Like `_select`, but for a source/destination pair that must agree on
        one vault — `move`/`copy` cannot cross vaults, so a conflicting URI on
        either side raises rather than silently picking one.
        """
        from chronon.core.vaults import split_vault_uri

        source_text = source if isinstance(source, str) else str(source)
        destination_text = (
            destination if isinstance(destination, str) else str(destination)
        )
        vault, source_plain = split_vault_uri(source_text, self._vault)
        vault, destination_plain = split_vault_uri(destination_text, vault)
        if self._store is None or vault != self._vault:
            self._vault = vault
            self._store = Store(self._root, vault=vault)
        return source_plain, destination_plain

    @property
    def root(self) -> Path:
        return self.store.root

    def _schema_path(self, resource: str) -> Path:
        return self.store.metadata / "schemas" / Path(resource + ".schema.json")

    def _schema(self, resource: str) -> dict[str, Any] | None:
        path = self._schema_path(resource)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FileError("registered schema is invalid", resource=resource) from exc
        if not isinstance(value, dict):
            raise FileError(
                "registered schema must be a JSON object", resource=resource
            )
        try:
            self._validate_schema_json(value)
        except FileError as exc:
            raise FileError(
                "registered schema is invalid",
                resource=resource,
                **exc.details,
            ) from exc
        return value

    def _working_content(self, resource: str) -> str:
        path = self.store.working_path(resource)
        try:
            return read_working_text(path)
        except FileNotFoundError as exc:
            raise FileError("working copy does not exist", resource=resource) from exc
        except OSError as exc:
            raise FileError("working copy is not readable", resource=resource) from exc

    def _validate(self, resource: str, content: str) -> None:
        issues = validate_content(content, resource, self._schema(resource))
        if issues:
            raise ValidationFailed(
                "resource validation failed",
                resource=resource,
                issues=[issue.as_dict() for issue in issues],
            )

    @staticmethod
    def _check_working_revision(
        resource: str, status: dict[str, Any], expected_revision: str | None
    ) -> None:
        actual = status["working_revision"]
        if expected_revision is None:
            if status["state"] in {"dirty", "untracked"}:
                raise PreconditionRequired(
                    "working copy contains uncommitted scratch changes",
                    resource=resource,
                    state=status["state"],
                    actual_revision=actual,
                    hint="read the working copy and retry with its working_revision as "
                    "expected_revision",
                )
            return
        if expected_revision != actual:
            raise RevisionConflict(
                "working copy changed since it was read",
                resource=resource,
                expected_revision=expected_revision,
                actual_revision=actual,
                state=status["state"],
                hint="read the working copy again before retrying",
            )

    def _with_working_state(
        self, result: dict[str, Any], resource: str
    ) -> dict[str, Any]:
        status = resource_state(self.store, resource)
        result.update(
            {
                "state": status["state"],
                "working_revision": status["working_revision"],
                "working_hash": status["working_hash"],
                "base_seq": status["latest_seq"],
            }
        )
        return result

    def add(self, resource: str | Path) -> dict[str, Any]:
        resource = self._select(resource)
        relative = self.store.normalize_resource(resource)
        content = self._working_content(relative)
        self._validate(relative, content)
        result = self.store.add(relative)
        if not result["created"]:
            result["state"] = resource_state(self.store, relative)["state"]
        return result

    def status(self, resource: str | Path) -> dict[str, Any]:
        resource = self._select(resource)
        relative = self.store.require_tracked(resource)
        result = resource_state(self.store, relative)
        if result["state"] == "missing":
            result["diff"] = None
            result["validation_issues"] = []
            return result
        commits = read_index(self.store, relative)
        if commits and result["state"] != "clean":
            latest = read_snapshot(self.store, relative, commits[-1])
            working = self._working_content(relative)
            issues = validate_content(working, relative, self._schema(relative))
            result["validation_issues"] = [issue.as_dict() for issue in issues]
            if issues:
                result["diff"] = None
            else:
                result["diff"] = compare_content(latest, working, relative).get(
                    "summary"
                )
        else:
            result["diff"] = None
            result["validation_issues"] = []
        return result

    def list_resources(self) -> dict[str, Any]:
        resources = [
            self.status(resource) for resource in self.store.tracked_resources()
        ]
        return {"root": str(self.root), "resources": resources}

    def list_directory(
        self, directory: str | Path = ".", include_status: bool = False
    ) -> dict[str, Any]:
        """List the immediate tracked children of a repository directory.

        Directories are virtual entries: they are included when at least one tracked
        resource exists below them, even though Chronon itself tracks only files.
        """
        directory = self._select(directory)
        relative_directory = self.store.normalize_directory(directory)
        directory_parts = (
            () if relative_directory == "." else tuple(Path(relative_directory).parts)
        )
        entries: dict[str, dict[str, str]] = {}
        for resource in self.store.tracked_resources():
            resource_parts = tuple(Path(resource).parts)
            if resource_parts[: len(directory_parts)] != directory_parts:
                continue
            remainder = resource_parts[len(directory_parts) :]
            if not remainder:
                continue
            name = remainder[0]
            entry_path = "/".join((*directory_parts, name))
            if len(remainder) == 1:
                entry = {
                    "name": name,
                    "path": entry_path,
                    "type": "file",
                }
                if include_status:
                    status = self.status(resource)
                    entry.update(
                        {
                            "state": status["state"],
                            "working_revision": status["working_revision"],
                            "history_count": status["history_count"],
                            "latest_commit_at": status["latest_commit_at"],
                        }
                    )
                entries[name] = entry
            else:
                entries[name] = {
                    "name": name,
                    "path": entry_path,
                    "type": "directory",
                }
        return {
            "root": str(self.root),
            "directory": relative_directory,
            "entries": [entries[name] for name in sorted(entries)],
        }

    @contextmanager
    def _lock_pair(self, first: str, second: str) -> Iterator[None]:
        """Lock two resources in a stable order so move/copy cannot deadlock."""
        with ExitStack() as stack:
            for relative in sorted({first, second}):
                stack.enter_context(resource_operation_lock(self.store, relative))
            yield

    def _move_schema(self, source: str, destination: str) -> bool:
        origin = self._schema_path(source)
        if not origin.is_file():
            return False
        target = self._schema_path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(origin, target)
        return True

    def _copy_schema(self, source: str, destination: str) -> bool:
        origin = self._schema_path(source)
        if not origin.is_file():
            return False
        target = self._schema_path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, target)
        return True

    @staticmethod
    def _prune_empty_parents(start: Path, stop: Path) -> None:
        current = start
        while current != stop and current.is_dir() and not any(current.iterdir()):
            current.rmdir()
            current = current.parent

    def _check_destination_free(self, destination: str) -> None:
        if self.store.is_tracked(destination):
            raise ResourceAlreadyTracked(
                "destination is already tracked by chronon", resource=destination
            )
        if (self.store.root / destination).exists():
            raise FileError(
                "destination path already exists",
                resource=destination,
                hint="move/copy will not overwrite an existing file",
            )
        meta = self.store.resource_dir(destination)
        if meta.exists() and any(meta.iterdir()):
            raise FileError(
                "leftover chronon metadata exists at the destination",
                resource=destination,
            )

    def _path_log(
        self, relative: str, descriptor: dict[str, Any] | None = None
    ) -> list[dict[str, str]]:
        """The resource's ``[{path, since}]`` timeline, oldest first.

        Pre-``path_log`` descriptors are synthesized from the first commit's
        timestamp so historical diffs still resolve a path.
        """
        descriptor = descriptor or self.store.read_descriptor(relative)
        log = descriptor.get("path_log")
        if isinstance(log, list) and log:
            return [dict(entry) for entry in log]
        commits = read_index(self.store, relative)
        since = commits[0].timestamp if commits else "0000-00-00T00:00:00.000000Z"
        return [{"path": descriptor.get("resource", relative), "since": since}]

    @staticmethod
    def _path_at(path_log: list[dict[str, str]], when: str | None) -> str:
        """Path in effect at ISO timestamp ``when`` (``None`` → the latest path)."""
        if not path_log:
            return ""
        if when is None:
            return path_log[-1]["path"]
        chosen = path_log[0]["path"]
        for entry in path_log:
            if entry["since"] <= when:
                chosen = entry["path"]
            else:
                break
        return chosen

    def move(self, source: str | Path, destination: str | Path) -> dict[str, Any]:
        """Rename a tracked file, carrying its full history and id along.

        The path change is appended to the descriptor's ``path_log`` so
        ``chronon diff`` can report the rename between two points in time.
        """
        source, destination = self._select_pair(source, destination)
        origin = self.store.require_tracked(source)
        target = self.store.normalize_resource(destination)
        if origin == target:
            raise InvalidArgument(
                "source and destination are the same path", resource=origin
            )
        with self._lock_pair(origin, target):
            origin_file = self.store.root / origin
            target_file = self.store.root / target
            if not origin_file.is_file():
                raise FileError("working copy does not exist", resource=origin)
            self._check_destination_free(target)

            origin_meta = self.store.resource_dir(origin)
            target_meta = self.store.resource_dir(target)
            descriptor = self.store.read_descriptor(origin)
            resource_id = descriptor.get("id") or new_resource_id()
            prior_log = self._path_log(origin, descriptor)

            target_meta.parent.mkdir(parents=True, exist_ok=True)
            target_file.parent.mkdir(parents=True, exist_ok=True)
            if target_meta.exists():
                target_meta.rmdir()
            os.rename(origin_meta, target_meta)
            moved_schema = self._move_schema(origin, target)
            try:
                os.rename(origin_file, target_file)
            except OSError as exc:
                os.rename(target_meta, origin_meta)
                if moved_schema:
                    self._move_schema(target, origin)
                raise FileError("cannot move working copy", resource=target) from exc

            descriptor["id"] = resource_id
            descriptor["resource"] = target
            descriptor["path_log"] = [
                *prior_log,
                {"path": target, "since": now_iso()},
            ]
            descriptor.pop("previous_paths", None)
            atomic_write_json(self.store.descriptor_path(target), descriptor)
            self.store.forget_id_cache(origin)
            self.store.forget_id_cache(target)
            self._prune_empty_parents(
                origin_meta.parent, self.store.metadata / "resources"
            )

            commits = read_index(self.store, target)
            return {
                "moved": True,
                "id": resource_id,
                "from": origin,
                "to": target,
                "path_log": descriptor["path_log"],
                "history_count": len(commits),
            }

    def copy(self, source: str | Path, destination: str | Path) -> dict[str, Any]:
        """Copy a tracked file to a new path as an independent resource.

        The copy is a fresh resource: new id, history starting at revision 0
        (no snapshots are branched). Its descriptor's ``copied_from`` records
        the source's id and the exact source revision the bytes came from, so
        the lineage is recoverable without linking the two histories.
        """
        source, destination = self._select_pair(source, destination)
        origin = self.store.require_tracked(source)
        target = self.store.normalize_resource(destination)
        if origin == target:
            raise InvalidArgument(
                "source and destination are the same path", resource=origin
            )
        with self._lock_pair(origin, target):
            origin_file = self.store.root / origin
            target_file = self.store.root / target
            if not origin_file.is_file():
                raise FileError("working copy does not exist", resource=origin)
            self._check_destination_free(target)

            target_meta = self.store.resource_dir(target)
            origin_descriptor = self.store.read_descriptor(origin)
            source_id = origin_descriptor.get("id") or new_resource_id()
            new_id = new_resource_id()
            source_status = resource_state(self.store, origin)

            try:
                content = read_working_text(origin_file)
            except OSError as exc:
                raise FileError(
                    "working copy is not readable", resource=origin
                ) from exc

            now = now_iso()
            copied_from = {
                "id": source_id,
                "path": origin,
                "revision": source_status["working_revision"],
                "seq": source_status["latest_seq"],
                "state": source_status["state"],
                "content_hash": content_hash(content),
                "at": now,
            }
            descriptor: dict[str, Any] = {
                "resource": target,
                "id": new_id,
                "path_log": [{"path": target, "since": now}],
                "copied_from": copied_from,
            }

            target_meta.mkdir(parents=True, exist_ok=True)
            atomic_write_json(target_meta / "resource.json", descriptor)
            atomic_write_json(
                target_meta / "state.json",
                {
                    "last_written_hash": content_hash(content),
                    "last_written_at": None,
                    "last_seq": 0,
                    "working_generation": 0,
                },
            )
            target_file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(target_file, content)
            self._copy_schema(origin, target)

            if origin_descriptor.get("id") != source_id:
                origin_descriptor["id"] = source_id
                atomic_write_json(self.store.descriptor_path(origin), origin_descriptor)
            self.store.forget_id_cache(target)

            return {
                "copied": True,
                "id": new_id,
                "source_id": source_id,
                "source_revision": source_status["working_revision"],
                "from": origin,
                "to": target,
                "history_count": 0,
            }

    def commit(
        self,
        resource: str | Path,
        message: str,
        author: str | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        resource = self._select(resource)
        relative = self.store.require_tracked(resource)
        if not message.strip():
            raise InvalidArgument("commit message must not be empty", resource=relative)
        with resource_operation_lock(self.store, relative):
            status = resource_state(self.store, relative)
            self._check_working_revision(relative, status, expected_revision)
            return self._commit_current(relative, message, author, status)

    def _commit_current(
        self,
        relative: str,
        message: str,
        author: str | None,
        status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = status or resource_state(self.store, relative)
        if status["state"] == "clean":
            raise NothingToCommit(
                "nothing to commit",
                resource=relative,
                state="clean",
                latest_seq=status["latest_seq"],
            )
        content = self._working_content(relative)
        self._validate(relative, content)
        commit = create_snapshot(
            self.store, relative, content, message, _author(author)
        )
        update_state(self.store, relative, content, commit.seq)
        return self._with_working_state(
            {
                "resource": relative,
                "committed": True,
                "commit": commit.as_dict(),
            },
            relative,
        )

    def _content_at(self, resource: str, ref: str | int) -> tuple[str, Commit | None]:
        commits = read_index(self.store, resource)
        resolved = resolve_revision(ref, commits, resource)
        if resolved.kind == "working":
            return self._working_content(resource), None
        if resolved.commit is None:  # defensive: resolve_revision owns this invariant
            raise FileError(
                "resolved revision is missing its commit",
                resource=resource,
                ref=str(ref),
            )
        return read_snapshot(self.store, resource, resolved.commit), resolved.commit

    def read(
        self, resource: str | Path, at: str | int = "working", format: str = "raw"
    ) -> dict[str, Any]:
        resource = self._select(resource)
        relative = self.store.require_tracked(resource)
        content, commit = self._content_at(relative, at)
        if format not in {"raw", "parsed"}:
            raise InvalidArgument("read format must be raw or parsed", format=format)
        value = content if format == "raw" else parse_content(content, relative)
        result = {
            "resource": relative,
            "revision": _commit_ref(str(at), commit),
            "content": value,
        }
        if isinstance(value, str):
            result["encoding"] = "utf-8"
            result["lossy"] = has_lone_surrogates(value)
        if commit is None:
            self._with_working_state(result, relative)
        return result

    def diff(
        self,
        resource: str | Path,
        from_ref: str | int = "latest",
        to_ref: str | int = "working",
        format: str = "auto",
    ) -> dict[str, Any]:
        resource = self._select(resource)
        relative = self.store.require_tracked(resource)
        commits = read_index(self.store, relative)
        if not commits and str(from_ref) == "latest":
            old = (
                "{}\n"
                if Path(relative).suffix.lower() in {".yaml", ".yml", ".json"}
                else ""
            )
            from_commit = None
        else:
            old, from_commit = self._content_at(relative, from_ref)
        new, to_commit = self._content_at(relative, to_ref)
        try:
            comparison = compare_content(
                old,
                new,
                relative,
                format=format,
                from_label=str(from_ref),
                to_label=str(to_ref),
            )
        except ValueError as exc:
            raise InvalidArgument(str(exc), value=format) from exc

        path_log = self._path_log(relative)
        from_when = from_commit.timestamp if from_commit else None
        to_when = to_commit.timestamp if to_commit else None
        from_path = self._path_at(path_log, from_when)
        to_path = self._path_at(path_log, to_when)

        return {
            "resource": relative,
            "from": _commit_ref(str(from_ref), from_commit),
            "to": _commit_ref(str(to_ref), to_commit),
            "path_change": {
                "from": from_path,
                "to": to_path,
                "changed": from_path != to_path,
            },
            **comparison,
        }

    def history(
        self,
        resource: str | Path,
        limit: int | None = None,
        since: str | None = None,
        until: str | None = None,
        author: str | None = None,
    ) -> dict[str, Any]:
        resource = self._select(resource)
        relative = self.store.require_tracked(resource)
        commits = read_index(self.store, relative)
        since_time = parse_time_filter(since)
        until_time = parse_time_filter(until)

        def commit_time(commit: Commit) -> datetime:
            return datetime.fromisoformat(commit.timestamp)

        filtered = [
            commit
            for commit in commits
            if (since_time is None or commit_time(commit) >= since_time)
            and (until_time is None or commit_time(commit) <= until_time)
            and (author is None or commit.author == author)
        ]
        filtered.reverse()
        if limit is not None:
            if limit < 0:
                raise InvalidArgument("limit must be non-negative", limit=limit)
            filtered = filtered[:limit]
        return {
            "resource": relative,
            "commits": [commit.as_dict() for commit in filtered],
        }

    def write(
        self,
        resource: str | Path,
        content: str,
        message: str | None = None,
        author: str | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        resource = self._select(resource)
        relative = self.store.require_tracked(resource)
        with resource_operation_lock(self.store, relative):
            return self._write_current(
                relative, content, message, author, expected_revision
            )

    def _write_current(
        self,
        relative: str,
        content: str,
        message: str | None,
        author: str | None,
        expected_revision: str | None,
    ) -> dict[str, Any]:
        status = resource_state(self.store, relative)
        if status["state"] == "foreign":
            raise ForeignChange(
                "working copy was modified outside chronon",
                resource=relative,
                hint="commit it, accept it (accept_foreign), or discard it with force",
            )
        self._check_working_revision(relative, status, expected_revision)
        self._validate(relative, content)
        atomic_write(self.store.working_path(relative), content)
        update_state(self.store, relative, content, status["latest_seq"] or 0)
        result: dict[str, Any] = {
            "resource": relative,
            "written": True,
            "committed": False,
        }
        if message is not None:
            try:
                committed = self._commit_current(relative, message, author)
                result.update(committed)
            except NothingToCommit:
                result["reason"] = "nothing_to_commit"
        return self._with_working_state(result, relative)

    def put(
        self,
        resource: str | Path,
        content: str,
        message: str | None = None,
        author: str | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        """Create a tracked resource or update one that is already tracked."""
        resource = self._select(resource)
        relative = self.store.normalize_resource(resource)
        with resource_operation_lock(self.store, relative):
            if self.store.is_tracked(relative):
                result = self._write_current(
                    relative, content, message, author, expected_revision
                )
                result["created"] = False
                return result

            return self._create_locked(
                relative, content, message, author, expected_revision
            )

    def create(
        self,
        resource: str | Path,
        content: str,
        message: str | None = None,
        author: str | None = None,
    ) -> dict[str, Any]:
        """Create and track a new resource; fail if it already exists."""
        resource = self._select(resource)
        relative = self.store.normalize_resource(resource)
        with resource_operation_lock(self.store, relative):
            if self.store.is_tracked(relative):
                raise ResourceAlreadyTracked(
                    "resource already exists",
                    resource=relative,
                    hint="read it and update it with write_resource",
                )
            return self._create_locked(relative, content, message, author, None)

    def _create_locked(
        self,
        relative: str,
        content: str,
        message: str | None,
        author: str | None,
        expected_revision: str | None,
    ) -> dict[str, Any]:
        working = self.store.working_path(relative)
        if working.exists():
            raise FileError(
                "refusing to overwrite an untracked path",
                resource=relative,
                hint=f"file exists on disk but is untracked; add {relative} to track it",
            )
        if expected_revision is not None:
            raise RevisionConflict(
                "resource does not exist yet",
                resource=relative,
                expected_revision=expected_revision,
                actual_revision=None,
                hint="omit the expected revision when creating a new resource",
            )
        if message is not None and not message.strip():
            raise InvalidArgument("commit message must not be empty", resource=relative)

        self._validate(relative, content)
        atomic_write(working, content)
        try:
            self.store.add(relative)
        except BaseException:
            # This path did not exist before creation, so removing only this newly
            # created file is safe if tracking metadata could not be initialized.
            working.unlink(missing_ok=True)
            raise

        result: dict[str, Any] = {
            "resource": relative,
            "created": True,
            "written": True,
            "tracked": True,
            "committed": False,
        }
        if message is not None:
            result.update(self._commit_current(relative, message, author))
        return self._with_working_state(result, relative)

    def discard(
        self,
        resource: str | Path,
        force: bool = False,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        resource = self._select(resource)
        relative = self.store.require_tracked(resource)
        with resource_operation_lock(self.store, relative):
            status = resource_state(self.store, relative)
            if status["state"] == "foreign" and not force:
                raise ForeignChange(
                    "refusing to discard a change made outside chronon",
                    resource=relative,
                    hint="pass force only if the external change may be lost",
                )
            if status["state"] != "foreign" or expected_revision is not None:
                self._check_working_revision(relative, status, expected_revision)
            commits = read_index(self.store, relative)
            if not commits:
                raise NothingToCommit(
                    "resource has no committed state to restore", resource=relative
                )
            latest = read_snapshot(self.store, relative, commits[-1])
            atomic_write(self.store.working_path(relative), latest)
            update_state(self.store, relative, latest, commits[-1].seq)
            return self._with_working_state(
                {
                    "resource": relative,
                    "discarded": True,
                    "seq": commits[-1].seq,
                },
                relative,
            )

    def accept_foreign(
        self,
        resource: str | Path,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        resource = self._select(resource)
        relative = self.store.require_tracked(resource)
        with resource_operation_lock(self.store, relative):
            status = resource_state(self.store, relative)
            if expected_revision is not None:
                self._check_working_revision(relative, status, expected_revision)
            if status["state"] != "foreign":
                return self._with_working_state(
                    {
                        "resource": relative,
                        "accepted": False,
                        "reason": "not_foreign",
                    },
                    relative,
                )
            content = self._working_content(relative)
            self._validate(relative, content)
            update_state(self.store, relative, content, status["latest_seq"] or 0)
            return self._with_working_state(
                {"resource": relative, "accepted": True}, relative
            )

    def rollback(
        self,
        resource: str | Path,
        at: str | int,
        message: str,
        author: str | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        resource = self._select(resource)
        relative = self.store.require_tracked(resource)
        if not message.strip():
            raise InvalidArgument("commit message must not be empty", resource=relative)
        if str(at) == "working":
            raise InvalidRevspec("rollback target cannot be working", value="working")
        with resource_operation_lock(self.store, relative):
            status = resource_state(self.store, relative)
            if status["state"] == "foreign" and expected_revision is None:
                raise ForeignChange(
                    "refusing to overwrite a change made outside chronon",
                    resource=relative,
                    hint="read status and retry with expected_revision, or commit/accept "
                    "the external change",
                )
            self._check_working_revision(relative, status, expected_revision)
            target, target_commit = self._content_at(relative, at)
            current = (
                None
                if status["state"] == "missing"
                else self._working_content(relative)
            )
            commits = read_index(self.store, relative)
            if (
                commits
                and target_commit
                and target_commit.content_hash == commits[-1].content_hash
            ):
                raise NothingToCommit(
                    "rollback target is identical to the latest commit",
                    resource=relative,
                    at=str(at),
                    latest_seq=commits[-1].seq,
                )
            if target == current:
                raise NothingToCommit(
                    "rollback target is already the working copy",
                    resource=relative,
                    at=str(at),
                )
            self._validate(relative, target)
            atomic_write(self.store.working_path(relative), target)
            commit = create_snapshot(
                self.store, relative, target, message, _author(author)
            )
            update_state(self.store, relative, target, commit.seq)
            result = {
                "resource": relative,
                "committed": True,
                "commit": commit.as_dict(),
                "restored_from": _commit_ref(str(at), target_commit),
            }
            return self._with_working_state(result, relative)

    def _dump(self, value: Any, resource: str) -> str:
        suffix = Path(resource).suffix.lower()
        if suffix == ".json":
            try:
                return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
            except (TypeError, ValueError) as exc:
                raise InvalidArgument(
                    "value cannot be represented in JSON", resource=resource
                ) from exc
        if suffix in {".yaml", ".yml"}:
            return yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
        if isinstance(value, str):
            return value
        raise FileError(
            "set/unset is supported only for YAML and JSON", resource=resource
        )

    def _typed_value(self, value: str, type_name: str | None) -> Any:
        try:
            if type_name is None:
                return yaml.safe_load(value)
            if type_name == "str":
                return value
            if type_name == "int":
                return int(value)
            if type_name == "float":
                return float(value)
            if type_name == "bool":
                lowered = value.lower()
                if lowered not in {"true", "false"}:
                    raise InvalidArgument(
                        "bool value must be true or false", value=value
                    )
                return lowered == "true"
            if type_name == "null":
                return None
            if type_name == "json":
                return json.loads(value)
        except (ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
            raise InvalidArgument(
                "value does not match the requested type", value=value, type=type_name
            ) from exc
        raise InvalidArgument("unknown value type", type=type_name)

    def set_value(
        self,
        resource: str | Path,
        path: str,
        value: str,
        type_name: str | None = None,
        message: str | None = None,
        author: str | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        resource = self._select(resource)
        relative = self.store.require_tracked(resource)
        with resource_operation_lock(self.store, relative):
            status = resource_state(self.store, relative)
            self._check_working_revision(relative, status, expected_revision)
            document = parse_content(self._working_content(relative), relative)
            updated = set_document_value(
                document, path, self._typed_value(value, type_name)
            )
            return self._write_current(
                relative,
                self._dump(updated, relative),
                message,
                author,
                status["working_revision"],
            )

    def unset_value(
        self,
        resource: str | Path,
        path: str,
        message: str | None = None,
        author: str | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        resource = self._select(resource)
        relative = self.store.require_tracked(resource)
        with resource_operation_lock(self.store, relative):
            status = resource_state(self.store, relative)
            self._check_working_revision(relative, status, expected_revision)
            document = parse_content(self._working_content(relative), relative)
            updated = unset_document_value(document, path)
            return self._write_current(
                relative,
                self._dump(updated, relative),
                message,
                author,
                status["working_revision"],
            )

    def path_history(
        self,
        resource: str | Path,
        path: str,
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, Any]:
        resource = self._select(resource)
        relative = self.store.require_tracked(resource)
        since_time = parse_time_filter(since)
        until_time = parse_time_filter(until)
        events: list[dict[str, Any]] = []
        previous: Any = MISSING
        for commit in read_index(self.store, relative):
            timestamp = datetime.fromisoformat(commit.timestamp)
            if until_time and timestamp > until_time:
                continue
            parsed = parse_content(
                read_snapshot(self.store, relative, commit), relative
            )
            try:
                current = get_value(parsed, path)
            except PathError:
                current = MISSING
            if (
                previous is not MISSING
                and current is not MISSING
                and current == previous
            ):
                continue
            if previous is MISSING and current is MISSING:
                continue
            if previous is MISSING:
                operation = "added"
            elif current is MISSING:
                operation = "removed"
            else:
                operation = "modified"
            if since_time is None or timestamp >= since_time:
                event = {
                    "seq": commit.seq,
                    "timestamp": commit.timestamp,
                    "author": commit.author,
                    "message": commit.message,
                    "op": operation,
                }
                if current is not MISSING:
                    event["value"] = current
                events.append(event)
            previous = current
        return {"resource": relative, "path": path, "events": events}

    def validate(self, resource: str | Path) -> dict[str, Any]:
        resource = self._select(resource)
        relative = self.store.require_tracked(resource)
        content = self._working_content(relative)
        issues = validate_content(content, relative, self._schema(relative))
        return {
            "resource": relative,
            "valid": not issues,
            "issues": [issue.as_dict() for issue in issues],
        }

    def register_schema(
        self, resource: str | Path, schema: dict[str, Any]
    ) -> dict[str, Any]:
        resource = self._select(resource)
        relative = self.store.require_tracked(resource)
        if not isinstance(schema, dict):
            raise FileError("schema must be a JSON object", resource=relative)
        content = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
        self._validate_schema_json(schema)
        with resource_operation_lock(self.store, relative):
            issues = validate_content(self._working_content(relative), relative, schema)
            if issues:
                raise ValidationFailed(
                    "current resource does not satisfy the schema",
                    resource=relative,
                    issues=[issue.as_dict() for issue in issues],
                )
            atomic_write(self._schema_path(relative), content)
        return {
            "resource": relative,
            "registered": True,
            "schema": str(self._schema_path(relative)),
        }

    @staticmethod
    def _validate_schema_json(schema: dict[str, Any]) -> None:
        from jsonschema import Draft202012Validator

        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            raise FileError("invalid JSON Schema", detail=str(exc)) from exc
