# Chronon

Chronon is local version control for text documents that AI agents edit,
especially Markdown. Every file gets its own linear, immutable history, and
every commit carries a message explaining *why* it changed. It works alongside
Git but neither calls nor replaces it.

![People use the CLI directly. Agents use MCP (preferred) or CLI. Both access a selected vault through Chronon, which keeps a separate immutable history for each file.](https://raw.githubusercontent.com/Hybrid3D/chronon-vcs/main/docs/images/chronon-overview.png)

- **Vault**: a named collection of managed files, such as `knowledge` or
  `research`. People and agents refer to it by name from any directory.
- **People** use the `chronon` CLI.
- **Agents** follow a generated `CHRONON.md` and use the `chronon-mcp` server
  (recommended) or the CLI. They never edit vault files directly.

> **Status: alpha.** Manual commit mode only. Chronon history is local and not
> pushed with Git, so see [Data and backups](#data-and-backups).

## Install

Requires Python 3.11+ on macOS, Linux, WSL2, or Windows.

```bash
uv tool install chronon-vcs   # or: pipx install chronon-vcs
```

See [docs/installation.md](https://github.com/Hybrid3D/chronon-vcs/blob/main/docs/installation.md)
for GitHub tags, platform setup, and uninstalling.

## Quick start

Create and register a vault:

```bash
mkdir -p ~/Documents/knowledge
chronon init ~/Documents/knowledge --register knowledge
```

### Use it yourself

```bash
chronon -v knowledge write first-note.md --content 'The pilot launches in October.' -m "add first note"
chronon -v knowledge read first-note.md
chronon -v knowledge log first-note.md
```

A `chronon://<vault>/<path>` URI names the same file without `-v`/`--vault`:
`chronon read chronon://knowledge/first-note.md` — handy for pointing at one
file from anywhere, including in a message to someone else.

Editing, diffs, and recovery are covered in the [CLI guide](https://github.com/Hybrid3D/chronon-vcs/blob/main/docs/cli.md).

### Let an agent use it

In the workspace where you run your agent (it does not need to be the vault):

```bash
chronon agent-setup --vault knowledge
claude mcp add --transport stdio chronon -- "$(command -v chronon-mcp)"
# Codex: codex mcp add chronon -- "$(command -v chronon-mcp)"
```

Then ask in plain language. There is no need to mention Chronon:

```text
Save these meeting notes in the knowledge vault as meeting-notes.md:
The pilot launches in October. Mina owns testing; Jae owns documentation.
```

Skip the MCP step and the agent falls back to the CLI. Re-run `agent-setup`
after upgrading Chronon to refresh the guidance.

No filesystem, or an agent that will not read `CHRONON.md` on its own? Print
the same guidance as text and paste it into the agent's instructions or system
prompt yourself:

```bash
chronon agent-instructions --vault knowledge
```

See [Agents and MCP](https://github.com/Hybrid3D/chronon-vcs/blob/main/docs/agents.md)
for `--allow-scratch` and the MCP equivalent, `get_agent_instructions`.

## How it works

- **One file, one history.** Commits are per file, need a message, and are never
  rewritten. `rollback` adds a new commit.
- **Safe concurrent edits.** Writes take the last-read `working_revision`
  (`--if-match` / `expected_revision`) and fail if the file changed since.
- **Outside edits are caught.** A change made in a normal editor shows up as
  `foreign` and is neither lost nor silently accepted.
- **Structured diffs.** YAML and JSON diff by value, and `set` / `path-history`
  work on individual keys. Optional JSON Schema validation runs before each write.
- **Time-based lookup.** Revisions can be `3`, `latest~2`, `2026-08-01`, or
  `7d ago`.

## Documentation

- [CLI guide](https://github.com/Hybrid3D/chronon-vcs/blob/main/docs/cli.md): editing
  workflow, file states, revisions, vault registry, command reference
- [Agents and MCP](https://github.com/Hybrid3D/chronon-vcs/blob/main/docs/agents.md):
  `agent-setup`, permissions, MCP configuration, and expected agent behavior
- [Installation](https://github.com/Hybrid3D/chronon-vcs/blob/main/docs/installation.md)
- [Contributing](https://github.com/Hybrid3D/chronon-vcs/blob/main/CONTRIBUTING.md):
  development setup and tests

## Data and backups

History lives in each vault's `.chronon/` directory, which Chronon adds to
`.gitignore`. **It is not pushed with Git.** Back up the working files and
`.chronon/` together. Content is not encrypted. Writes are atomic and locked
across processes, but there is no multi-file transaction.

## License

[MIT](LICENSE)
