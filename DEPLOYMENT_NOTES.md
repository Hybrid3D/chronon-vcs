# Deployment notes

This file records release decisions that should remain explicit instead of being
silently guessed during preparation. It is not an end-user guide.

## Recommended direction already applied

- Keep the package distribution name `chronon-vcs` and the executable name
  `chronon`; changing either now would create avoidable upgrade ambiguity.
- Keep manual mode as the only accepted mode. The existing `auto` configuration
  shape is left reserved, but no background watcher is implied or advertised.
- Keep MCP local over stdio. A network transport would require authentication,
  authorization, request limits, and a separate threat model.
- Require Python 3.11+ and test every currently declared minor version in CI.
- Use isolated per-user CLI installers (`uv tool install` / `pipx install`) as
  the primary user path, installed from a pinned Git tag until PyPI publication;
  keep editable pip installs for contributors.
- Make tagging the whole release trigger. `.github/workflows/release.yml` builds
  the distributions, checks the tag against `__version__`, smoke-tests the wheel,
  attaches the artifacts to the GitHub Release, and publishes to PyPI through
  trusted publishing (no stored API token).
- Use English for the primary public `README.md` so one set of Windows/Linux/WSL/
  macOS instructions serves the broadest GitHub audience. Add a linked
  `README.ko.md` later if Korean localization is wanted; avoid maintaining two
  divergent primary guides before the commands stabilize.
- Keep optimistic concurrency tokens (`working_revision`) in both CLI and MCP,
  and use native inter-process file locks underneath them.
- Do not publish, create a remote, tag, or change version `0.2.1` in this worktree.
- Assume the public repository is `Hybrid3D/chronon-vcs`. That name is currently
  written into `pyproject.toml` and `README.md`; change both places together if
  a different owner/name is chosen.

## Code review findings addressed

- Replaced the Windows-only in-process lock fallback with cross-platform native
  inter-process locking. Concurrent CLI and MCP processes now serialize the same
  resource on every supported OS.
- Locked vault registry read-modify-write operations so concurrent registrations
  cannot silently erase one another.
- Prevented rollback from overwriting an unobserved foreign edit, and made a
  supplied revision mandatory to honor even when `discard --force` is used.
- Added optional revision matching to `accept`/`accept_foreign`.
- Report missing tracked working files as `missing` instead of making an all-file
  status fail; the latest commit can be restored with `discard`.
- Stopped silently skipping corrupt descriptors. Added validation for repository
  format, state metadata, schemas, commit records, hashes, timestamps, and
  snapshot filenames.
- Confined snapshot reads to their resource metadata directory, including when a
  local snapshot path is replaced by a symlink.
- Made generic CLI file/parse failures machine-readable under `--json` and made
  malformed generated-document markers fail without rewriting user content.
- Filled MCP workflow gaps: initialization, create-or-update, directory listing,
  schema registration, and agent-guidance generation now share the CLI operation
  layer.
- Raised dependency lower bounds to combinations verified by clean-environment
  smoke tests; the former Typer minimum crashed while rendering `--help`, and the
  former MCP minimum could not register the annotated tools.

## Decisions to make before the first public release

1. **Repository URL and package ownership.** `[project.urls]` now points at
   `https://github.com/Hybrid3D/chronon-vcs`. Confirm that this is the final
   GitHub organization/repository, and that PyPI ownership of `chronon-vcs`
   matches, before the first tag is pushed.
2. **Local-only versus portable history.** `.chronon/` is currently added to
   `.gitignore`. This is coherent for private local history, but users need a
   documented backup/sync story if history is expected to survive machines.
   Recommended default: remain local for the alpha release and add an explicit
   export/import command before encouraging synchronization.
3. **Untrack semantics.** There is intentionally no command that deletes a
   resource's history. Recommended design: `chronon archive` should move metadata
   into a recoverable archive; a separate `purge --force` may permanently delete
   it. Do not ship a deceptively harmless `remove` command first.
4. **Schema lifecycle.** Registration exists, but list/replace/removal policy is
   minimal. Add `schema-show`, `schema-list`, and a guarded `schema-remove` after
   deciding whether historical commits retain the schema version used at commit
   time.
5. **MCP SDK v2 migration.** The project deliberately remains on `mcp>=1.14,<2`.
   The official v2 line is a major rewrite. Migrate in a dedicated change with
   protocol-level client tests rather than widening the dependency bound during
   release preparation.
6. **Recovery and integrity tooling.** Add a read-only `chronon verify` command
   that checks every index, snapshot hash, descriptor, and schema and produces a
   machine-readable report. Current reads verify snapshot hashes on access and
   list operations no longer hide corrupt descriptors, but there is no one-shot
   full-vault audit.
7. **Crash transactions.** Atomic file replacement and locking prevent common
   corruption, but multi-file operations (`mv`, snapshot + index + state) do not
   have a journal. Add recovery tests with injected failures before claiming
   database-like durability.
8. **Binary and very large files.** Chronon assumes UTF-8 text and stores full
   snapshots. Non-UTF-8 bytes are tolerated and round-trip losslessly
   (surrogateescape), but there is no binary-asset mode: no object
   deduplication, size limits, streaming, or "binary files differ" diffs. Keep
   that limit prominent until those are designed.
9. **Security policy.** Before public release, add a real security contact and
   supported-version policy. Chronon stores plaintext snapshots and should never
   imply encryption or secret management.

## Release checklist

- [ ] Confirm the GitHub owner/repository written into the project URLs.
- [ ] Confirm the PyPI name `chronon-vcs` is available and owned by the publisher.
- [ ] Register the PyPI trusted publisher for this repository and create the
      `pypi` GitHub environment used by `.github/workflows/release.yml`.
- [ ] Set the intended release version once in `src/chronon/__init__.py`, then
      tag `v<version>`; the release workflow rejects a mismatch.
- [ ] Run all GitHub Actions jobs, especially native Windows locking tests.
- [ ] Run `python -m build` and `python -m twine check dist/*` from a clean clone.
- [ ] Run `python -m pip_audit . --skip-editable` and review dependency licenses.
- [ ] Install the wheel into a fresh environment and exercise CLI plus MCP tool listing.
- [ ] Review wheel/sdist contents.
- [x] Add `SECURITY.md` with a GitHub private vulnerability-reporting route.
- [ ] Create signed/tagged release notes; do not publish from an unreviewed worktree.
