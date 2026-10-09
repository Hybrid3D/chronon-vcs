# Using Chronon with AI agents

Agents learn the Chronon workflow from a generated `CHRONON.md`, then use the
`chronon-mcp` server (preferred) or the `chronon` CLI.

## `agent-setup`

Run it in the workspace where the agent starts. It does not need to be a vault.

```bash
chronon agent-setup --vault knowledge   # fix one target vault
chronon agent-setup                     # agent uses whichever vault the user names
```

```console
$ chronon agent-setup --vault knowledge
Created /home/me/work/client-app/CHRONON.md
Updated /home/me/work/client-app/CLAUDE.md -> CHRONON.md
```

`CHRONON.md` is linked from every instruction file that already exists
(`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.github/copilot-instructions.md`), or
from `AGENTS.md` if none exist. Choose targets with `--link FILE` (repeatable)
or skip with `--no-link`.

Generated content sits between `<!-- chronon:... -->` markers, so re-running
`agent-setup` refreshes it without touching your own text. **Re-run it after
every Chronon upgrade**, and do not copy the workflow into `CLAUDE.md` or
`AGENTS.md` by hand, since a hand-written copy goes stale silently.

### Useful flags

```bash
chronon agent-setup --vault knowledge --permissions   # allowlist safe commands in Claude Code
chronon agent-setup --check                           # report stale files, write nothing (exit 1)
```

`--permissions` merges rules into `.claude/settings.local.json`, respecting
existing `deny` rules. It allows read-only commands and writes that leave an
undoable commit. `discard`, `accept`, vault-registry commands, and `admin` still
require approval.

### Guidance as text

To get the same guidance without writing a file (for a system prompt, for
example):

```bash
chronon agent-instructions --vault knowledge [--allow-scratch]
```

The MCP equivalent is `get_agent_instructions`. By default the text requires
the agent to finish each change with a commit; `--allow-scratch` also documents
uncommitted scratch writes.

## MCP server

`chronon-mcp` is a local stdio server that the client launches itself. Register
it with the absolute path from `command -v chronon-mcp`
(PowerShell: `(Get-Command chronon-mcp).Source`):

```bash
claude mcp add --transport stdio chronon -- "/absolute/path/to/chronon-mcp"   # Claude Code
codex mcp add chronon -- "/absolute/path/to/chronon-mcp"                      # Codex CLI
```

Generic client configuration:

```json
{ "mcpServers": { "chronon": { "command": "/absolute/path/to/chronon-mcp" } } }
```

Every tool accepts an optional `vault` name. Tools cover setup
(`list_vaults`, `add_vault`, `get_agent_instructions`, ...), reading
(`read_resource`, `status_resource`, `diff_resource`, `history_resource`, ...),
writing (`create_resource` (new files), `write_resource` (existing files), `set_value`, `commit_resource`, `move_resource`, ...),
and recovery (`rollback_resource`, `discard_changes`, `accept_foreign`,
`validate_resource`). A result with an `error` field is a failed operation even
if the MCP call itself succeeded.

## What a well-behaved agent does

1. Uses MCP if available, otherwise the CLI, without mixing them in one change.
2. Uses the vault the user named and never guesses one.
3. Creates a new file with `create_resource` (no revision needed); for an
   existing file, reads first and keeps `working_revision`.
4. Passes it as `expected_revision` (MCP) or `--if-match` (CLI) when updating.
5. Checks the diff, validates, and commits with a meaningful message.
6. On `revision_conflict`, re-reads and reconciles instead of retrying.

For clients that do not load instruction files automatically, attach
`CHRONON.md` or start the prompt with "First read CHRONON.md and follow it."

The generated rules are guidance for the agent, not a security boundary. An
agent with the same filesystem permissions as you can still reach the raw files.
