from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from chronon.api.operations import ChrononRepository
from chronon.api.operations import add_vault as _add_vault
from chronon.api.operations import get_agent_instructions as _get_agent_instructions
from chronon.api.operations import init_repository as _init_repository
from chronon.api.operations import list_vaults as _list_vaults
from chronon.api.operations import remove_vault as _remove_vault
from chronon.api.operations import write_agent_instructions as _write_agent_instructions
from chronon.core.docs import ADMIN_AGENT_RULE
from chronon.core.errors import ChrononError
from chronon.core.text import json_safe

mcp = FastMCP(
    "chronon",
    instructions=(
        f"Rule 1: {ADMIN_AGENT_RULE} "
        "Chronon MCP manages local vault resources with immutable per-file "
        "history. Use these MCP tools, not direct filesystem writes or the "
        "Chronon CLI, for managed resources. Use the exact user-selected vault "
        "name when needed; never guess. Before mutation, read the resource or "
        "status, retain working_revision, and pass it "
        "as expected_revision. To create a NEW file use create_resource (no "
        "expected_revision); write_resource only updates existing tracked files. "
        "Prefer one-shot writes with a meaningful message. "
        "On revision_conflict, re-read and reconcile; never retry blindly. Any "
        "result with an error field is a failed operation. Call "
        "get_agent_instructions for the full text usage guide (optionally "
        "scoped to one vault with the vault argument) before starting a task "
        "in an unfamiliar vault."
    ),
)


def _safe(action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return json_safe(action())
    except ChrononError as exc:
        return exc.as_dict()


@mcp.tool()
def list_vaults() -> dict[str, Any]:
    """List registered vault names without exposing their storage paths."""
    return _safe(lambda: _list_vaults())


@mcp.tool()
def add_vault(name: str, path: str) -> dict[str, Any]:
    """Register an initialized directory without changing an existing name's path."""
    return _safe(lambda: _add_vault(name, path))


@mcp.tool()
def remove_vault(name: str) -> dict[str, Any]:
    """Remove a name from the global vault registry (repository itself is untouched)."""
    return _safe(lambda: _remove_vault(name))


@mcp.tool()
def initialize_repository(
    directory: str = ".",
    register_vault: str | None = None,
) -> dict[str, Any]:
    """Initialize a manual-mode repository and optionally register it as a vault."""
    return _safe(
        lambda: _init_repository(
            directory,
            "manual",
            register_vault=register_vault,
        )
    )


@mcp.tool()
def add_resource(
    resource: str,
    vault: str | None = None,
) -> dict[str, Any]:
    """Start tracking a file that already exists on disk (does not write content).

    To create a new file, use ``create_resource``.
    """
    return _safe(lambda: ChrononRepository(vault=vault).add(resource))


@mcp.tool()
def move_resource(
    source: str,
    destination: str,
    vault: str | None = None,
) -> dict[str, Any]:
    """Rename a tracked file, preserving its full history and its resource id."""
    return _safe(lambda: ChrononRepository(vault=vault).move(source, destination))


@mcp.tool()
def copy_resource(
    source: str,
    destination: str,
    vault: str | None = None,
) -> dict[str, Any]:
    """Copy a tracked file to a new path as a fresh resource.

    The copy gets a new id and a history starting at revision 0; its descriptor
    records the source id and the source revision the content came from.
    """
    return _safe(lambda: ChrononRepository(vault=vault).copy(source, destination))


@mcp.tool()
def create_resource(
    resource: str,
    content: str,
    message: str | None = None,
    author: str | None = None,
    vault: str | None = None,
) -> dict[str, Any]:
    """Create and track a NEW file; fails if the path already exists.

    Use this for the first write of a file. It needs no ``expected_revision``
    because there is nothing to read yet. Supplying ``message`` also creates the
    first immutable commit. To change an existing file, use ``write_resource``.
    """
    return _safe(
        lambda: ChrononRepository(vault=vault).create(
            resource, content, message, author
        )
    )


@mcp.tool()
def put_resource(
    resource: str,
    content: str,
    message: str | None = None,
    author: str | None = None,
    expected_revision: str | None = None,
    vault: str | None = None,
) -> dict[str, Any]:
    """DEPRECATED: use ``create_resource`` (new file) or ``write_resource`` (existing).

    Creates a new resource or updates a tracked one. Kept only for existing
    clients; it will be removed in a future release. Do not pass
    ``expected_revision`` when the path does not exist yet.
    """
    return _safe(
        lambda: ChrononRepository(vault=vault).put(
            resource, content, message, author, expected_revision
        )
    )


@mcp.tool()
def read_resource(
    resource: str,
    at: str = "working",
    format: str = "raw",
    vault: str | None = None,
) -> dict[str, Any]:
    """Read a working copy or a committed revision."""
    return _safe(lambda: ChrononRepository(vault=vault).read(resource, at, format))


@mcp.tool()
def diff_resource(
    resource: str,
    from_ref: str = "latest",
    to_ref: str = "working",
    format: str = "auto",
    vault: str | None = None,
) -> dict[str, Any]:
    """Compare two revisions; YAML and JSON use structural diff by default."""
    return _safe(
        lambda: ChrononRepository(vault=vault).diff(resource, from_ref, to_ref, format)
    )


@mcp.tool()
def history_resource(
    resource: str,
    limit: int | None = None,
    since: str | None = None,
    until: str | None = None,
    author: str | None = None,
    vault: str | None = None,
) -> dict[str, Any]:
    """List immutable commits, newest first."""
    return _safe(
        lambda: ChrononRepository(vault=vault).history(
            resource, limit, since, until, author
        )
    )


@mcp.tool()
def commit_resource(
    resource: str,
    message: str,
    author: str | None = None,
    expected_revision: str | None = None,
    vault: str | None = None,
) -> dict[str, Any]:
    """Commit the current working copy. A non-empty message is required."""
    return _safe(
        lambda: ChrononRepository(vault=vault).commit(
            resource, message, author, expected_revision
        )
    )


@mcp.tool()
def write_resource(
    resource: str,
    content: str,
    message: str | None = None,
    author: str | None = None,
    expected_revision: str | None = None,
    vault: str | None = None,
) -> dict[str, Any]:
    """Replace the content of an EXISTING tracked file (read it first).

    Pass the ``working_revision`` from ``read_resource`` as ``expected_revision``.
    This cannot create a file: use ``create_resource`` for a new path.
    """
    return _safe(
        lambda: ChrononRepository(vault=vault).write(
            resource, content, message, author, expected_revision
        )
    )


@mcp.tool()
def set_value(
    resource: str,
    path: str,
    value: str,
    type_name: str | None = None,
    message: str | None = None,
    author: str | None = None,
    expected_revision: str | None = None,
    vault: str | None = None,
) -> dict[str, Any]:
    """Set one YAML or JSON value using Chronon's dot-path syntax."""
    return _safe(
        lambda: ChrononRepository(vault=vault).set_value(
            resource,
            path,
            value,
            type_name,
            message,
            author,
            expected_revision,
        )
    )


@mcp.tool()
def unset_value(
    resource: str,
    path: str,
    message: str | None = None,
    author: str | None = None,
    expected_revision: str | None = None,
    vault: str | None = None,
) -> dict[str, Any]:
    """Remove one YAML or JSON value using Chronon's dot-path syntax."""
    return _safe(
        lambda: ChrononRepository(vault=vault).unset_value(
            resource, path, message, author, expected_revision
        )
    )


@mcp.tool()
def status_resource(
    resource: str,
    vault: str | None = None,
) -> dict[str, Any]:
    """Report untracked, clean, dirty, or foreign working-copy state."""
    return _safe(lambda: ChrononRepository(vault=vault).status(resource))


@mcp.tool()
def list_resources(
    vault: str | None = None,
) -> dict[str, Any]:
    """List all tracked resources and their states."""
    return _safe(lambda: ChrononRepository(vault=vault).list_resources())


@mcp.tool()
def list_directory(
    directory: str = ".",
    include_status: bool = False,
    vault: str | None = None,
) -> dict[str, Any]:
    """List immediate tracked children below one repository directory."""
    return _safe(
        lambda: ChrononRepository(vault=vault).list_directory(directory, include_status)
    )


@mcp.tool()
def path_history(
    resource: str,
    path: str,
    since: str | None = None,
    until: str | None = None,
    vault: str | None = None,
) -> dict[str, Any]:
    """List commits that changed one structured value."""
    return _safe(
        lambda: ChrononRepository(vault=vault).path_history(
            resource, path, since, until
        )
    )


@mcp.tool()
def rollback_resource(
    resource: str,
    at: str,
    message: str,
    author: str | None = None,
    expected_revision: str | None = None,
    vault: str | None = None,
) -> dict[str, Any]:
    """Restore a historical revision and record the restoration as a new commit."""
    return _safe(
        lambda: ChrononRepository(vault=vault).rollback(
            resource, at, message, author, expected_revision
        )
    )


@mcp.tool()
def discard_changes(
    resource: str,
    force: bool = False,
    expected_revision: str | None = None,
    vault: str | None = None,
) -> dict[str, Any]:
    """Restore the latest committed content; foreign changes require force."""
    return _safe(
        lambda: ChrononRepository(vault=vault).discard(
            resource, force, expected_revision
        )
    )


@mcp.tool()
def accept_foreign(
    resource: str,
    expected_revision: str | None = None,
    vault: str | None = None,
) -> dict[str, Any]:
    """Accept externally edited content as the dirty working baseline."""
    return _safe(
        lambda: ChrononRepository(vault=vault).accept_foreign(
            resource, expected_revision
        )
    )


@mcp.tool()
def validate_resource(
    resource: str,
    vault: str | None = None,
) -> dict[str, Any]:
    """Validate the current working copy without changing it."""
    return _safe(lambda: ChrononRepository(vault=vault).validate(resource))


@mcp.tool()
def register_schema(
    resource: str,
    schema: dict[str, Any],
    vault: str | None = None,
) -> dict[str, Any]:
    """Register a valid JSON Schema after validating the current resource."""
    return _safe(
        lambda: ChrononRepository(vault=vault).register_schema(resource, schema)
    )


@mcp.tool()
def write_agent_instructions(
    directory: str = ".",
    vault: str | None = None,
    link: bool = True,
) -> dict[str, Any]:
    """Write CHRONON.md in an external directory, optionally for one vault.

    With `link` (default), the instruction files that AI clients load on their
    own (CLAUDE.md, AGENTS.md, ...) also get a short pointer block, because they
    will not discover CHRONON.md otherwise.
    """
    return _safe(lambda: _write_agent_instructions(directory, vault=vault, link=link))


@mcp.tool()
def get_agent_instructions(
    vault: str | None = None,
    allow_scratch: bool = False,
) -> dict[str, Any]:
    """Return the Chronon usage guide for AI agents as text, writing nothing.

    The result's `instructions` field is plain text meant to be pasted directly
    into this agent's own instructions or system prompt. `allow_scratch`
    documents uncommitted scratch writes as an available action when set; it
    defaults to off, so the guidance always finishes a change with a commit.
    """
    return _safe(
        lambda: {
            "instructions": _get_agent_instructions(
                vault=vault, allow_scratch=allow_scratch
            )
        }
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
