"""Generate/refresh Chronon guidance in an external agent working directory.

The default file is ``CHRONON.md`` in a caller-selected directory. That
directory need not be a Chronon vault: in the common case it is the external
workspace from which an agent accesses a registered vault. Callers that embed
the section in another guidance file may still select a different filename.

There are two target-selection variants. The generic section explains how to
select a vault; the vault-specific section fixes one registered vault name and
puts that selector into every example. Both are interface-adaptive: an agent
uses Chronon's MCP tools when they are available and falls back to the CLI when
they are not.

Writing the guide is only half of the setup: an AI client loads its own
instruction file (``CLAUDE.md``, ``AGENTS.md``, …) and will not discover
``CHRONON.md`` on its own. So this module also writes a short pointer block into
those files, which is what makes the guide actually reach the agent without the
user wiring it up — and re-copying it — by hand.

Both blocks are delimited by HTML comment markers so re-running the command
(e.g. after a chronon upgrade) updates only chronon's own block and leaves
the rest of the file — including content the user wrote by hand — untouched.
"""

from __future__ import annotations

import os.path
from pathlib import Path
from typing import Any

from .errors import FileError, InvalidArgument
from .store import atomic_write

BEGIN_MARKER = "<!-- chronon:agents-md:begin -->"
END_MARKER = "<!-- chronon:agents-md:end -->"
LINK_BEGIN_MARKER = "<!-- chronon:link:begin -->"
LINK_END_MARKER = "<!-- chronon:link:end -->"
DEFAULT_AGENTS_MD = "CHRONON.md"

# Shared by the generated guide, its client pointers, and MCP instructions.
ADMIN_AGENT_RULE = (
    "Never invoke the Chronon admin command group, directly or indirectly. "
    "Do not bypass this rule through scripts, library calls, delegated agents, "
    "or direct access to the vault registry. Do not discover or reconstruct "
    "physical vault paths. Use vault names and vault-relative resource paths. "
    "If administration is required, ask the human administrator to perform it "
    "outside the agent session; do not ask them to disclose physical paths."
)

# Instruction files AI clients load by themselves. `chronon agent-setup` links the
# generated guide from whichever of these already exist, so the user does not
# have to wire it up (and re-copy it) by hand.
KNOWN_AGENT_FILES = (
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    ".github/copilot-instructions.md",
)
# Used when a workspace has none of the above yet: AGENTS.md is the one filename
# read by the widest set of clients, including Claude Code and Codex.
FALLBACK_AGENT_FILE = "AGENTS.md"
# Files whose client resolves `@path` as a real import instead of plain text.
IMPORTING_AGENT_FILES = frozenset({"CLAUDE.md"})


def _render_section(vault: str | None, *, allow_scratch: bool = True) -> str:
    command = f"chronon --vault {vault}" if vault else "chronon"
    heading = f"Chronon vault `{vault}`" if vault else "Chronon"
    mcp_vault = f', vault="{vault}"' if vault else ', vault="<selected-vault>"'
    if vault:
        context = f"""This workspace uses the registered Chronon vault **`{vault}`**. All paths
below are relative to that vault's root, regardless of the current directory.
For MCP calls, always pass `vault="{vault}"`. For CLI calls, pass
`--vault {vault}` either before or after the subcommand; the examples below use
the leading form consistently.

Do not inspect or modify managed resources with direct filesystem tools. Use
one Chronon interface for the entire task so tracking, scratch safety, revision
preconditions, and history remain effective."""
        selection = f"""### Selected vault

The vault is fixed for this workspace:

```text
MCP: pass vault="{vault}" to every repository tool
CLI: {command} <subcommand> ...
```

For example:

```bash
{command} ls .
{command} status
```

Do not substitute another vault unless the user explicitly changes the target."""
        mcp_target_note = f"""Every repository call below includes `vault="{vault}"`.
Keep that argument even when the MCP server happens to start inside a vault."""
        uri_note = f"""A resource argument may also be written as
`chronon://{vault}/<path>` instead of a bare path — matching this workspace's
fixed vault, so it resolves the same way. A `chronon://` URI naming a
*different* vault is rejected; this workspace stays fixed to `{vault}` unless
the user explicitly changes the target."""
    else:
        context = """This workspace may use documents managed by **chronon**: a local,
per-document, time-indexed history independent of git. When the user identifies
the target vault, access its managed resources through one Chronon interface
rather than direct filesystem tools. This preserves tracking, scratch safety,
revision preconditions, and history."""
        selection = """### Selecting a vault

If the user supplies a registered vault name, use that exact name on every
repository operation:

```text
MCP: pass vault="<selected-vault>" to every repository tool
CLI: pass --vault <selected-vault> before or after the subcommand
```

CLI example:

```bash
chronon --vault <vault> ls .
chronon --vault <vault> status
```

When no named vault is specified and the active interface is running inside a
vault, omit the MCP `vault` argument or CLI `--vault` option and let Chronon
discover it from the working directory. This also works from a plain workspace
directory that was pinned to one vault with `chronon set-vault <name>`
(writes `.chronon-workspace`); check for that pin before asking the user which
vault to use. Do not guess a vault name."""
        mcp_target_note = """In the calls below, replace `<selected-vault>` with the
user-selected name. Omit the `vault` argument only when Chronon can discover the
intended vault from the MCP server's working directory."""
        uri_note = """A resource argument may also carry its own vault inline:
`chronon://<vault>/<path>` (for example, `chronon://notes/apartment.md`) names
resource `apartment.md` in vault `notes` directly — as an MCP tool's `resource`
argument or a CLI positional argument — with no separate `vault=`/`--vault`
needed. Use this when a document, a chat message, or another agent's
instructions point at one managed file by this notation. It works even when no
vault would otherwise be discoverable (no `--vault`, no cwd vault, no
workspace pin): the URI alone is enough. An explicitly given vault that
conflicts with the URI's vault is rejected rather than silently resolved."""

    if allow_scratch:
        commit_default_bullet = """- Default to committing whenever you change a tracked resource's content,
  even if the user's request never said "commit" — an edit request implies a
  finished, saved edit. Leave a resource as an uncommitted scratch/`dirty` edit
  only when the user explicitly asked to stage or draft without saving it, or
  the task is plainly unfinished. Never wait to be told to commit."""
    else:
        commit_default_bullet = """- Always finish a change with a commit; scratch (uncommitted) writes are not
  part of this workflow. Never call `write_resource`/`chronon write` without a
  commit message, and never leave a resource `dirty` on purpose — not even if
  asked to "just draft" or "save without committing." If a task is genuinely
  unfinished, say so instead of leaving an uncommitted edit."""

    mcp_rows = [
        f'| List managed files | `list_directory(directory="."{mcp_vault})` |',
        f'| Read current content | `read_resource(resource="<path>"{mcp_vault})` |',
        "| Create a NEW file (first write; no revision needed) | "
        f'`create_resource(resource="<path>", content="...", message="..."{mcp_vault})` |',
        "| Start tracking a file that already exists on disk | "
        f'`add_resource(resource="<path>"{mcp_vault})` |',
        f'| Rename a tracked file | `move_resource(source="<old>", '
        f'destination="<new>"{mcp_vault})` |',
        f'| Copy to a new independent resource | `copy_resource(source="<src>", '
        f'destination="<dst>"{mcp_vault})` |',
    ]
    if allow_scratch:
        mcp_rows.append(
            "| Save scratch content | "
            f'`write_resource(resource="<path>", content="...", '
            f'expected_revision="..."{mcp_vault})` |'
        )
    mcp_rows += [
        "| Replace content of an existing file + commit | "
        f'`write_resource(resource="<path>", content="...", message="...", '
        f'expected_revision="..."{mcp_vault})` |',
        "| Change one structured value + commit | "
        f'`set_value(resource="<path>", path="a.b.c", value="X", message="...", '
        f'expected_revision="..."{mcp_vault})` |',
        "| Commit existing scratch content | "
        f'`commit_resource(resource="<path>", message="...", '
        f'expected_revision="..."{mcp_vault})` |',
        f'| Current state | `status_resource(resource="<path>"{mcp_vault})` |',
        "| Diff revisions | "
        f'`diff_resource(resource="<path>", from_ref="latest~3", '
        f'to_ref="working"{mcp_vault})` |',
        "| Read a past revision | "
        f'`read_resource(resource="<path>", at="latest~1"{mcp_vault})` |',
        "| History of one value | "
        f'`path_history(resource="<path>", path="a.b.c"{mcp_vault})` |',
        f'| Full commit log | `history_resource(resource="<path>"{mcp_vault})` |',
        f'| Validate current content | `validate_resource(resource="<path>"{mcp_vault})` |',
        "| Discard uncommitted edits | "
        f'`discard_changes(resource="<path>", expected_revision="..."{mcp_vault})` |',
        "| Restore an old revision + commit | "
        f'`rollback_resource(resource="<path>", at="<rev>", message="...", '
        f'expected_revision="..."{mcp_vault})` |',
        "| Accept an external edit | after reading its revision, "
        f'`accept_foreign(resource="<path>", expected_revision="..."{mcp_vault})`; '
        "read again before the next write |",
    ]
    mcp_table = "\n".join(mcp_rows)

    cli_rows = [
        f"| List managed files | `{command} ls [directory]` |",
        f"| Read current content | `{command} read <path>` |",
        "| Create a NEW file (first write; no --if-match) | "
        f'`{command} write <path> --stdin --message "..."` |',
        f"| Start tracking a file already on disk | `{command} add <path>` |",
        f"| Rename a tracked file (keeps history) | `{command} mv <old> <new>` |",
        f"| Copy a tracked file to a new path | `{command} cp <src> <dst>` |",
    ]
    if allow_scratch:
        cli_rows.append(
            "| Save scratch content without committing | "
            f"`{command} write <path> --stdin --scratch --if-match <working_revision>` |"
        )
    cli_rows += [
        "| Replace content + commit in one step | "
        f'`{command} write <path> --stdin --message "..." --if-match <working_revision>` |',
        "| Change one value + commit | "
        f'`{command} set <path> --path "a.b.c" --value X --message "..." '
        "--if-match <working_revision>` |",
        "| Commit an existing scratch edit | "
        f'`{command} commit <path> --message "..." --if-match <working_revision>` |',
        f"| Current state | `{command} status <path>` "
        "(untracked / clean / dirty / foreign / missing) |",
        "| Diff against a point in time | "
        f"`{command} diff <path> --from 2026-08-01 --to working` |",
        f"| Diff against N commits ago | `{command} diff <path> --from latest~3` |",
        f"| Read past content | `{command} show <path> <rev>` |",
        f"| History of one value | `{command} path-history <path> --path a.b.c` |",
        f"| Full commit log | `{command} log <path>` |",
        f"| Discard uncommitted edits | `{command} discard <path>` |",
        "| Restore an old revision (as a new commit) | "
        f'`{command} rollback <path> <rev> --message "..."` |',
        "| Accept an edit made outside Chronon | get `working_revision`, then "
        f"`{command} accept <path> --if-match <working_revision>`; "
        "read again before the next write |",
    ]
    cli_table = "\n".join(cli_rows)

    return f"""{BEGIN_MARKER}
## {heading} (per-file version history)

### Rule 1: Human administration only

{ADMIN_AGENT_RULE}

{context}

### Choose one interface

1. If Chronon MCP tools are available, use them for the whole task. Tool names
   may be prefixed by the client, but include names such as `read_resource`,
   `write_resource`, and `commit_resource`.
2. Otherwise use the `chronon` CLI commands below.
3. Do not invoke the CLI merely to probe for Chronon when its MCP tools are
   available, and do not alternate between MCP and CLI during one write flow.

> **IMPORTANT — chronon is plumbing. Run it, don't talk about it.**
> Chronon is only *how* you read and save tracked files, the same way `ls`
> and `cat` are how you look around. You don't tell the user "I ran `ls`,
> then I ran `cat`" — so don't tell them "chronon status를 했습니다",
> "chronon ls 실행", "chronon write로 커밋했습니다" either. Just do it and
> report the actual work.
>
> - **Never narrate Chronon tools or commands** in your replies to the user or
>   in a task summary. "아파트/매매정보.md의 매도호가를 갱신했습니다" — yes.
>   "`write_resource`/`{command} write`로 커밋했습니다" — no.
> - **Never put chronon into anything you write**: not into file contents,
>   documents, reports, commit messages, or PR text. A sentence like
>   "갱신은 chronon을 통해서만 (`chronon write ...`)" must never appear in a
>   deliverable. The reader must not need to know chronon exists.
> - Describing *what you looked at* in plain terms is fine — "vault의
>   README.md를 확인했습니다" is OK; naming the chronon command you
>   used to read it is not.
> - **Never announce a commit, even in plain language** — not just the tool
>   name. "수정 후 커밋했습니다" or "커밋까지 완료했습니다" leaks the
>   underlying version-control mechanism just as much as naming
>   `commit_resource` does. Report the edit itself and nothing else: "매도호가를
>   8억 2천으로 갱신했습니다", not "...갱신하고 커밋했습니다". This holds
>   whether or not the user's request happened to say the word "commit" —
>   translate that intent into "save/finalize the edit", not into a status
>   line about committing.
> - Chronon tool results and CLI `--json` output are for you to act on, then
>   discard — like shell output, not like results to hand over.

{commit_default_bullet}
- A commit always needs a non-empty message (`message` with MCP, `--message`
  with the CLI). Nothing is saved to history automatically. A Chronon scratch
  edit is `dirty`; a normal editor or another program produces `foreign`. Both
  are uncommitted until you explicitly commit them.
- History is per file, not per repository: each tracked file has its own
  independent, linear, immutable timeline. There is no multi-file atomic commit.
- For YAML/JSON, diffs are structural (`path: old -> new`), not line-based text.
- `move_resource`/`chronon mv` keeps history; a rename spanning the diff range
  shows as a `renamed: old -> new` line. `copy_resource`/`chronon cp` makes a
  fresh resource (new id, history from revision 0) that records where it was
  copied from.
- Prefer one-shot writes with a commit message over separate save-then-commit;
  message cost is near zero for an agent, and it keeps history dense and useful.
- Creating a NEW file is different from editing one: use `create_resource`
  (CLI: `write` on a new path) with the full content and a commit message, and
  pass no `expected_revision` / `--if-match` — there is nothing to read yet, and
  supplying one fails. `write_resource` only updates files that are already
  tracked and fails on a new path. `add_resource` only starts tracking a file that
  already exists on disk; it never writes content. If the path exists, switch to
  the read-then-write flow below. `put_resource` is deprecated; do not use it.
- Before mutating a tracked file, read it or inspect status and retain the opaque
  `working_revision`. With MCP pass it back as `expected_revision`; with the CLI
  pass it as `--if-match`. On `revision_conflict`, re-read and reconcile; never
  retry a stale overwrite blindly.
- An MCP result containing an `error` field is a failed operation even if the
  transport call itself succeeded. Resolve it before continuing.

With the CLI, prefer `--json` when you need structured state or a
`working_revision` token.

{selection}

### Referencing a resource with a `chronon://` URI

{uri_note}

### MCP tools (preferred when available)

The calls below use Python-like notation only to show arguments; call the actual
MCP tools exposed by the client.

{mcp_target_note}

| Task | MCP tool call |
|---|---|
{mcp_table}

### CLI fallback (when MCP tools are unavailable)

| Task | Command |
|---|---|
{cli_table}

`<rev>` accepts an integer seq, `working`, `latest`, `latest~N`, an ISO date/timestamp,
or a relative time like `"7d ago"`.

When using the CLI, run `chronon --help` or `chronon <command> --help` for the
full command list.
{END_MARKER}"""


def render_section(*, allow_scratch: bool = True) -> str:
    """Render generic guidance that explains how a vault is selected.

    ``allow_scratch`` controls whether the guidance documents uncommitted
    scratch writes as an available action; existing state left `dirty` or
    `foreign` by someone else is still explained either way, since that can
    happen regardless of what this agent itself is allowed to do.
    """
    return _render_section(None, allow_scratch=allow_scratch)


def render_vault_section(vault: str, *, allow_scratch: bool = True) -> str:
    """Render guidance specialized for one registered vault name."""
    return _render_section(vault, allow_scratch=allow_scratch)


def _plan_block(path: Path, begin: str, end: str, block: str) -> str | None:
    """Return the full file content ``block`` implies, or None if already current.

    Content outside the markers is never touched, so a file that also holds
    hand-written instructions survives every regeneration. Splitting this out
    from the write lets `--check` answer "is this stale?" without touching disk.
    """
    if not path.exists():
        return f"# {path.name}\n\n{block}\n"

    try:
        existing = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FileError("cannot read agent instructions", path=str(path)) from exc
    start = existing.find(begin)
    stop = existing.find(end)
    marker_counts = (existing.count(begin), existing.count(end))
    if marker_counts not in {(0, 0), (1, 1)} or (
        start != -1 and stop != -1 and stop < start
    ):
        raise InvalidArgument(
            "agent instructions contain malformed chronon markers",
            path=str(path),
            hint="repair or remove the chronon marker block, then retry",
        )
    if start != -1 and stop != -1:
        stop += len(end)
        updated = existing[:start] + block + existing[stop:]
    else:
        separator = (
            ""
            if existing.endswith("\n\n")
            else ("\n" if existing.endswith("\n") else "\n\n")
        )
        updated = existing + separator + block + "\n"
    return None if updated == existing else updated


def _check_block(path: Path, begin: str, end: str, block: str) -> dict[str, Any]:
    """Report whether ``path`` already holds the current block. Writes nothing."""
    if not path.exists():
        return {"path": str(path), "status": "missing", "current": False}
    status = "current" if _plan_block(path, begin, end, block) is None else "stale"
    return {"path": str(path), "status": status, "current": status == "current"}


def _upsert_block(path: Path, begin: str, end: str, block: str) -> dict[str, Any]:
    """Create ``path`` around ``block``, or replace an existing marked block."""
    created = not path.exists()
    content = _plan_block(path, begin, end, block)
    if content is None:
        return {"path": str(path), "created": False, "updated": False}
    try:
        atomic_write(path, content)
    except OSError as exc:
        raise FileError("cannot write agent instructions", path=str(path)) from exc
    return {"path": str(path), "created": created, "updated": True}


def _resolve_root(root: Path) -> Path:
    resolved = Path(root).expanduser().resolve()
    if not resolved.is_dir():
        raise InvalidArgument(
            "agent instructions destination must be an existing directory",
            path=str(resolved),
        )
    return resolved


def _agents_md_plan(root: Path, filename: str, vault: str | None) -> tuple[Path, str]:
    root = _resolve_root(root)
    name = Path(filename).name
    if not name or name != filename:
        raise InvalidArgument("invalid agent instructions filename", filename=filename)
    section = render_vault_section(vault) if vault else render_section()
    return root / name, section


def ensure_agents_md(
    root: Path,
    filename: str = DEFAULT_AGENTS_MD,
    vault: str | None = None,
) -> dict[str, Any]:
    """Create the agents doc if missing, or update chronon's section in place.

    ``filename`` is the doc's name at ``root`` and defaults to ``CHRONON.md``;
    pass e.g. ``"AGENTS.md"`` or ``"CLAUDE.md"`` to target another file.

    Never touches content outside the markers, so hand-written instructions in
    the rest of the file survive repeated `chronon agent-setup` calls.
    """
    path, section = _agents_md_plan(root, filename, vault)
    return _upsert_block(path, BEGIN_MARKER, END_MARKER, section)


def check_agents_md(
    root: Path,
    filename: str = DEFAULT_AGENTS_MD,
    vault: str | None = None,
) -> dict[str, Any]:
    """Report whether the agents doc is current, without writing anything."""
    path, section = _agents_md_plan(root, filename, vault)
    return _check_block(path, BEGIN_MARKER, END_MARKER, section)


def render_link_section(
    doc_reference: str,
    vault: str | None = None,
    importing: bool = False,
) -> str:
    """Render the short pointer block placed in a client's own instruction file.

    ``doc_reference`` is the generated guide's path relative to the file holding
    the pointer. ``importing`` selects `@path` import syntax for clients that
    resolve it (Claude Code) instead of a plain Markdown link.
    """
    heading = (
        f"Chronon vault `{vault}` (managed documents)"
        if vault
        else "Chronon-managed documents"
    )
    target = f" The vault is **`{vault}`**." if vault else ""
    href = doc_reference if doc_reference.startswith(".") else f"./{doc_reference}"
    pointer = (
        f"@{doc_reference}"
        if importing
        else f"Read and follow [`{doc_reference}`]({href}) first."
    )
    return f"""{LINK_BEGIN_MARKER}
## {heading}

**Rule 1:** {ADMIN_AGENT_RULE}

Managed documents in this workspace are read and written through **chronon**,
never through direct filesystem tools.{target} The commands, interface order,
and safety rules live in `{doc_reference}`; follow that file whenever a managed
document is involved.

{pointer}

`{doc_reference}` is generated by `chronon agent-setup` and is refreshed when
Chronon is upgraded. Do not copy or paraphrase its instructions into this file;
a hand-written copy goes stale without warning.
{LINK_END_MARKER}"""


def _relative_reference(target: Path, doc: Path) -> str:
    """Path of ``doc`` as written inside ``target``, using forward slashes."""
    return Path(os.path.relpath(doc, target.parent)).as_posix()


def resolve_link_target(root: Path, target: str) -> Path:
    """Validate one instruction-file target and resolve it under ``root``."""
    candidate = Path(target)
    if candidate.is_absolute() or not target or target != candidate.as_posix():
        raise InvalidArgument(
            "link target must be a relative path inside the destination",
            target=target,
        )
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise InvalidArgument(
            "link target must stay inside the destination directory",
            target=target,
        )
    if resolved.is_dir():
        raise InvalidArgument("link target is a directory", target=target)
    return resolved


def detect_link_targets(root: Path, exclude: str = DEFAULT_AGENTS_MD) -> list[str]:
    """Instruction files to link from: the ones already present, else a default.

    Returning ``FALLBACK_AGENT_FILE`` for an unconfigured workspace is what makes
    a single `chronon agent-setup` produce a setup the agent actually loads.
    """
    root = _resolve_root(root)
    found = [
        name
        for name in KNOWN_AGENT_FILES
        if name != exclude and (root / name).is_file()
    ]
    if found:
        return found
    return [] if FALLBACK_AGENT_FILE == exclude else [FALLBACK_AGENT_FILE]


def _link_plan(
    root: Path, target: str, filename: str, vault: str | None
) -> tuple[Path, str, str]:
    root = _resolve_root(root)
    path = resolve_link_target(root, target)
    doc = root / Path(filename).name
    if path == doc:
        raise InvalidArgument(
            "link target cannot be the generated guide itself",
            target=target,
        )
    reference = _relative_reference(path, doc)
    importing = path.name in IMPORTING_AGENT_FILES
    return (
        path,
        render_link_section(reference, vault=vault, importing=importing),
        reference,
    )


def ensure_agent_link(
    root: Path,
    target: str,
    filename: str = DEFAULT_AGENTS_MD,
    vault: str | None = None,
) -> dict[str, Any]:
    """Point one client instruction file at the generated guide.

    The pointer is a marked block, so re-running the command updates it in place
    and leaves the rest of the client's instructions alone.
    """
    path, block, reference = _link_plan(root, target, filename, vault)
    result = _upsert_block(path, LINK_BEGIN_MARKER, LINK_END_MARKER, block)
    result["target"] = target
    result["references"] = reference
    return result


def check_agent_link(
    root: Path,
    target: str,
    filename: str = DEFAULT_AGENTS_MD,
    vault: str | None = None,
) -> dict[str, Any]:
    """Report whether one instruction file's pointer is current. Writes nothing."""
    path, block, reference = _link_plan(root, target, filename, vault)
    result = _check_block(path, LINK_BEGIN_MARKER, LINK_END_MARKER, block)
    result["target"] = target
    result["references"] = reference
    return result
