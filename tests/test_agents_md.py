import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from chronon.api.operations import (
    check_agent_instructions,
    get_agent_instructions,
    init_repository,
    write_agent_instructions,
)
from chronon.cli import app
from chronon.core.docs import (
    ADMIN_AGENT_RULE,
    BEGIN_MARKER,
    END_MARKER,
    LINK_BEGIN_MARKER,
    LINK_END_MARKER,
    detect_link_targets,
    ensure_agent_link,
    ensure_agents_md,
    render_section,
    render_vault_section,
)
from chronon.core.errors import InvalidArgument

runner = CliRunner()


@pytest.mark.parametrize("vault", [None, "knowledge"])
def test_agent_setup_puts_admin_prohibition_first_in_guide_and_links(
    tmp_path: Path, monkeypatch, vault: str | None
) -> None:
    monkeypatch.setenv("CHRONON_CONFIG_HOME", str(tmp_path / "config"))
    if vault:
        init_repository(tmp_path / "private storage", register_vault=vault)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    write_agent_instructions(
        workspace, vault=vault, link_targets=["AGENTS.md", "CLAUDE.md"]
    )

    guide = (workspace / "CHRONON.md").read_text(encoding="utf-8")
    assert ADMIN_AGENT_RULE in guide
    assert guide.index("### Rule 1:") < guide.index("### Choose one interface")
    for filename in ("AGENTS.md", "CLAUDE.md"):
        pointer = (workspace / filename).read_text(encoding="utf-8")
        assert f"**Rule 1:** {ADMIN_AGENT_RULE}" in pointer
        assert pointer.index("**Rule 1:**") < pointer.index("Managed documents")


def test_ensure_agents_md_creates_file(tmp_path: Path) -> None:
    init_repository(tmp_path)
    result = ensure_agents_md(tmp_path)
    assert result == {
        "path": str(tmp_path / "CHRONON.md"),
        "created": True,
        "updated": True,
    }
    content = (tmp_path / "CHRONON.md").read_text(encoding="utf-8")
    assert content.startswith("# CHRONON.md\n")
    assert BEGIN_MARKER in content
    assert END_MARKER in content
    assert "chronon add <path>" in content
    assert "chronon is plumbing" in content.lower()
    assert "never narrate chronon tools or commands" in content.lower()
    assert "If Chronon MCP tools are available" in content
    assert "### MCP tools (preferred when available)" in content
    assert "### CLI fallback (when MCP tools are unavailable)" in content
    assert 'read_resource(resource="<path>", vault="<selected-vault>")' in content
    assert "chronon --vault <vault> ls ." in content
    assert "Do not guess a vault name" in content
    assert "working_revision" in content
    assert "expected_revision" in content
    assert "revision_conflict" in content


def test_vault_template_is_specialized_for_one_vault() -> None:
    content = render_vault_section("knowledge")

    assert "registered Chronon vault **`knowledge`**" in content
    assert "chronon --vault knowledge ls ." in content
    assert "chronon --vault knowledge read <path>" in content
    assert "chronon --vault knowledge write <path>" in content
    assert 'read_resource(resource="<path>", vault="knowledge")' in content
    assert 'write_resource(resource="<path>"' in content
    assert 'vault="knowledge")' in content
    assert 'create_resource(resource="<path>", content="..."' in content
    assert "put_resource` is deprecated" in content
    assert "direct filesystem tools" in content
    assert "<vault>" not in content
    assert "<selected-vault>" not in content


def test_ensure_agents_md_custom_filename(tmp_path: Path) -> None:
    init_repository(tmp_path)
    result = ensure_agents_md(tmp_path, "AGENTS.md")
    assert result["path"] == str(tmp_path / "AGENTS.md")
    content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert content.startswith("# AGENTS.md\n")
    assert BEGIN_MARKER in content
    assert not (tmp_path / "CHRONON.md").exists()


def test_ensure_agents_md_rejects_path_as_filename(tmp_path: Path) -> None:
    init_repository(tmp_path)
    with pytest.raises(InvalidArgument):
        ensure_agents_md(tmp_path, "sub/AGENTS.md")


def test_ensure_agents_md_is_idempotent(tmp_path: Path) -> None:
    init_repository(tmp_path)
    ensure_agents_md(tmp_path)
    before = (tmp_path / "CHRONON.md").read_text(encoding="utf-8")

    result = ensure_agents_md(tmp_path)

    assert result["created"] is False
    assert result["updated"] is False
    assert (tmp_path / "CHRONON.md").read_text(encoding="utf-8") == before


def test_ensure_agents_md_appends_to_existing_file_without_marker(
    tmp_path: Path,
) -> None:
    init_repository(tmp_path)
    (tmp_path / "CHRONON.md").write_text(
        "# My rules\n\nAlways ask first.\n", encoding="utf-8"
    )

    result = ensure_agents_md(tmp_path)

    assert result == {
        "path": str(tmp_path / "CHRONON.md"),
        "created": False,
        "updated": True,
    }
    content = (tmp_path / "CHRONON.md").read_text(encoding="utf-8")
    assert content.startswith("# My rules\n\nAlways ask first.\n")
    assert BEGIN_MARKER in content


def test_ensure_agents_md_preserves_content_outside_markers_on_refresh(
    tmp_path: Path,
) -> None:
    init_repository(tmp_path)
    ensure_agents_md(tmp_path)
    path = tmp_path / "CHRONON.md"
    original = path.read_text(encoding="utf-8")
    edited = "# My Notes\n\nDo not delete this line.\n\n" + original
    path.write_text(edited, encoding="utf-8")

    result = ensure_agents_md(tmp_path)

    assert result["updated"] is False  # section content unchanged, so no rewrite
    content = path.read_text(encoding="utf-8")
    assert content.startswith("# My Notes\n\nDo not delete this line.\n\n")
    assert content.count(BEGIN_MARKER) == 1


@pytest.mark.parametrize(
    "content",
    [
        f"{BEGIN_MARKER}\nmissing end\n",
        f"{END_MARKER}\nwrong order\n{BEGIN_MARKER}\n",
        f"{BEGIN_MARKER}\none\n{END_MARKER}\n{BEGIN_MARKER}\ntwo\n{END_MARKER}\n",
    ],
)
def test_ensure_agents_md_rejects_malformed_markers(
    tmp_path: Path, content: str
) -> None:
    init_repository(tmp_path)
    path = tmp_path / "CHRONON.md"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(InvalidArgument, match="malformed chronon markers"):
        ensure_agents_md(tmp_path)

    assert path.read_text(encoding="utf-8") == content


def test_write_agent_instructions_targets_external_directory(tmp_path: Path) -> None:
    destination = tmp_path / "workspace"
    destination.mkdir()

    result = write_agent_instructions(destination)

    assert result["created"] is True
    assert result["path"] == str(destination / "CHRONON.md")


def test_write_agent_instructions_validates_and_describes_vault(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CHRONON_CONFIG_HOME", str(tmp_path / "config"))
    vault = tmp_path / "vault"
    destination = tmp_path / "workspace"
    destination.mkdir()
    init_repository(vault, register_vault="knowledge")

    result = write_agent_instructions(destination, vault="knowledge")

    assert result["path"] == str(destination / "CHRONON.md")
    content = (destination / "CHRONON.md").read_text(encoding="utf-8")
    assert "chronon --vault knowledge ls ." in content
    assert not (vault / "CHRONON.md").exists()


# ── CLI ──────────────────────────────────────────────────────────────────


def test_cli_agents_md_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    created = runner.invoke(app, ["agent-setup"])
    assert created.exit_code == 0
    assert "Created" in created.output
    assert (tmp_path / "CHRONON.md").is_file()

    unchanged = runner.invoke(app, ["agent-setup"])
    assert unchanged.exit_code == 0
    assert "already up to date" in unchanged.output


def test_cli_agents_md_command_custom_path(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "external-project"
    destination.mkdir()
    monkeypatch.chdir(tmp_path)

    created = runner.invoke(app, ["agent-setup", str(destination)])
    assert created.exit_code == 0, created.output
    assert "Created" in created.output
    assert (destination / "CHRONON.md").is_file()
    assert not (tmp_path / "CHRONON.md").exists()
    # A workspace with no instruction file still ends up loading the guide.
    assert (destination / "AGENTS.md").is_file()
    assert not (tmp_path / "AGENTS.md").exists()


def test_cli_agents_md_vault_option_writes_to_current_directory(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CHRONON_CONFIG_HOME", str(tmp_path / "config"))
    root = tmp_path / "proj"
    assert runner.invoke(app, ["init", str(root), "--register", "mine"]).exit_code == 0

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = runner.invoke(app, ["agent-setup", "--vault", "mine"])
    assert result.exit_code == 0, result.output
    assert (elsewhere / "CHRONON.md").is_file()
    assert not (root / "CHRONON.md").exists()
    content = (elsewhere / "CHRONON.md").read_text(encoding="utf-8")
    assert "chronon --vault mine ls ." in content
    assert "chronon --vault mine read <path>" in content


def test_cli_agents_md_accepts_global_vault_option_and_custom_path(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CHRONON_CONFIG_HOME", str(tmp_path / "config"))
    root = tmp_path / "proj"
    assert runner.invoke(app, ["init", str(root), "--register", "mine"]).exit_code == 0

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    destination = tmp_path / "agent-project"
    destination.mkdir()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--vault", "mine", "agent-setup", str(destination)])
    assert result.exit_code == 0, result.output
    assert (destination / "CHRONON.md").is_file()
    assert "chronon --vault mine" in (destination / "CHRONON.md").read_text(
        encoding="utf-8"
    )


# ── linking the guide into client instruction files ──────────────────────


def test_detect_link_targets_prefers_existing_instruction_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "CLAUDE.md").write_text("# CLAUDE.md\n", encoding="utf-8")
    (tmp_path / "GEMINI.md").write_text("# GEMINI.md\n", encoding="utf-8")

    assert detect_link_targets(tmp_path) == ["CLAUDE.md", "GEMINI.md"]


def test_detect_link_targets_falls_back_to_agents_md(tmp_path: Path) -> None:
    assert detect_link_targets(tmp_path) == ["AGENTS.md"]


def test_detect_link_targets_excludes_the_guide_itself(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# AGENTS.md\n", encoding="utf-8")

    assert detect_link_targets(tmp_path, exclude="AGENTS.md") == []


def test_ensure_agent_link_uses_import_syntax_for_claude(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text(
        "# CLAUDE.md\n\nProject rules stay here.\n", encoding="utf-8"
    )

    result = ensure_agent_link(tmp_path, "CLAUDE.md", vault="knowledge")

    assert result["created"] is False
    assert result["updated"] is True
    assert result["references"] == "CHRONON.md"
    content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert content.startswith("# CLAUDE.md\n\nProject rules stay here.\n")
    assert LINK_BEGIN_MARKER in content
    assert LINK_END_MARKER in content
    assert "@CHRONON.md" in content
    assert "The vault is **`knowledge`**." in content
    assert "Do not copy or paraphrase" in content
    # The pointer stays short; the workflow itself lives in CHRONON.md only.
    assert BEGIN_MARKER not in content


def test_ensure_agent_link_uses_a_plain_link_for_other_clients(
    tmp_path: Path,
) -> None:
    result = ensure_agent_link(tmp_path, "AGENTS.md")

    assert result["created"] is True
    content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "@CHRONON.md" not in content
    assert "[`CHRONON.md`](./CHRONON.md)" in content


def test_ensure_agent_link_resolves_a_nested_target_relatively(
    tmp_path: Path,
) -> None:
    result = ensure_agent_link(tmp_path, ".github/copilot-instructions.md")

    assert result["references"] == "../CHRONON.md"
    content = (tmp_path / ".github" / "copilot-instructions.md").read_text(
        encoding="utf-8"
    )
    assert "[`../CHRONON.md`](../CHRONON.md)" in content


def test_ensure_agent_link_is_idempotent(tmp_path: Path) -> None:
    ensure_agent_link(tmp_path, "AGENTS.md")
    before = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    result = ensure_agent_link(tmp_path, "AGENTS.md")

    assert result["updated"] is False
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == before


def test_ensure_agent_link_refreshes_only_its_own_block(tmp_path: Path) -> None:
    ensure_agent_link(tmp_path, "CLAUDE.md")
    path = tmp_path / "CLAUDE.md"
    path.write_text(
        path.read_text(encoding="utf-8") + "\n## House rules\n\nKeep Korean.\n",
        encoding="utf-8",
    )

    ensure_agent_link(tmp_path, "CLAUDE.md", vault="knowledge")

    content = path.read_text(encoding="utf-8")
    assert content.count(LINK_BEGIN_MARKER) == 1
    assert "The vault is **`knowledge`**." in content
    assert content.endswith("## House rules\n\nKeep Korean.\n")


@pytest.mark.parametrize("target", ["../escape.md", "/etc/passwd", "CHRONON.md"])
def test_ensure_agent_link_rejects_unsafe_targets(tmp_path: Path, target: str) -> None:
    with pytest.raises(InvalidArgument):
        ensure_agent_link(tmp_path, target)


def test_write_agent_instructions_links_detected_files(tmp_path: Path) -> None:
    destination = tmp_path / "workspace"
    destination.mkdir()
    (destination / "CLAUDE.md").write_text("# CLAUDE.md\n", encoding="utf-8")

    result = write_agent_instructions(destination)

    assert [link["target"] for link in result["links"]] == ["CLAUDE.md"]
    assert "@CHRONON.md" in (destination / "CLAUDE.md").read_text(encoding="utf-8")
    assert not (destination / "AGENTS.md").exists()


def test_write_agent_instructions_can_skip_linking(tmp_path: Path) -> None:
    destination = tmp_path / "workspace"
    destination.mkdir()

    result = write_agent_instructions(destination, link=False)

    assert "links" not in result
    assert not (destination / "AGENTS.md").exists()


def test_cli_agents_md_links_and_reports_each_target(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# CLAUDE.md\n", encoding="utf-8")

    result = runner.invoke(app, ["agent-setup"])

    assert result.exit_code == 0, result.output
    assert "Created" in result.output
    assert "CLAUDE.md -> CHRONON.md" in result.output
    assert "@CHRONON.md" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    unchanged = runner.invoke(app, ["agent-setup"])
    assert "Unchanged" in unchanged.output


def test_cli_agents_md_explicit_link_targets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# CLAUDE.md\n", encoding="utf-8")

    result = runner.invoke(app, ["agent-setup", "--link", "GEMINI.md"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "GEMINI.md").is_file()
    assert LINK_BEGIN_MARKER not in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")


def test_cli_agents_md_no_link(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["agent-setup", "--no-link"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "CHRONON.md").is_file()
    assert not (tmp_path / "AGENTS.md").exists()


def test_cli_agents_md_rejects_link_with_no_link(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["agent-setup", "--no-link", "--link", "AGENTS.md"])

    assert result.exit_code == 4
    assert not (tmp_path / "AGENTS.md").exists()


# ── --check: report staleness without writing ────────────────────────────


def test_check_reports_missing_setup(tmp_path: Path) -> None:
    result = check_agent_instructions(tmp_path)

    assert result["current"] is False
    assert result["status"] == "missing"
    assert [link["status"] for link in result["links"]] == ["missing"]
    assert list(tmp_path.iterdir()) == []


def test_check_reports_a_complete_setup_as_current(tmp_path: Path) -> None:
    write_agent_instructions(tmp_path, vault=None)

    result = check_agent_instructions(tmp_path)

    assert result["current"] is True
    assert result["status"] == "current"
    assert all(link["current"] for link in result["links"])


def test_check_detects_a_hand_edited_block(tmp_path: Path) -> None:
    write_agent_instructions(tmp_path)
    guide = tmp_path / "CHRONON.md"
    guide.write_text(
        guide.read_text(encoding="utf-8").replace("chronon add <path>", "chronon nope"),
        encoding="utf-8",
    )

    result = check_agent_instructions(tmp_path)

    assert result["status"] == "stale"
    assert result["current"] is False


def test_check_detects_a_stale_pointer_only(tmp_path: Path) -> None:
    write_agent_instructions(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# AGENTS.md\n", encoding="utf-8")

    result = check_agent_instructions(tmp_path)

    assert result["status"] == "current"
    # The file is still there; only its pointer block was lost.
    assert result["links"][0]["status"] == "stale"
    assert result["current"] is False


def test_check_writes_nothing(tmp_path: Path) -> None:
    write_agent_instructions(tmp_path)
    before = {
        path.name: path.read_text(encoding="utf-8") for path in tmp_path.iterdir()
    }

    check_agent_instructions(tmp_path, vault=None)

    after = {path.name: path.read_text(encoding="utf-8") for path in tmp_path.iterdir()}
    assert after == before


def test_check_honors_no_link(tmp_path: Path) -> None:
    write_agent_instructions(tmp_path, link=False)

    result = check_agent_instructions(tmp_path, link=False)

    assert result["current"] is True
    assert "links" not in result


def test_cli_agents_md_check_exits_nonzero_when_stale(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    missing = runner.invoke(app, ["agent-setup", "--check"])
    assert missing.exit_code == 1
    assert "missing" in missing.output
    assert "out of date" in missing.output
    assert not (tmp_path / "CHRONON.md").exists()

    assert runner.invoke(app, ["agent-setup"]).exit_code == 0

    current = runner.invoke(app, ["agent-setup", "--check"])
    assert current.exit_code == 0, current.output
    assert "current" in current.output
    assert "out of date" not in current.output


def test_cli_agents_md_check_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["agent-setup"])

    result = runner.invoke(app, ["agent-setup", "--check", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["current"] is True
    assert payload["checked"] is True
    assert payload["links"][0]["references"] == "CHRONON.md"


# ── text-output guidance (chronon agent-instructions / get_agent_instructions) ──


def test_render_section_still_documents_scratch_by_default() -> None:
    """`agent-setup`'s CHRONON.md keeps its existing default; only the
    text-output command (`get_agent_instructions`) defaults the toggle off."""
    guide = render_section()

    assert "Save scratch content" in guide
    assert "--stdin --scratch" in guide
    assert "Default to committing whenever you change" in guide


def test_render_section_allow_scratch_false_removes_the_scratch_actions() -> None:
    guide = render_section(allow_scratch=False)

    assert "Save scratch content" not in guide
    assert "--stdin --scratch" not in guide
    assert "Always finish a change with a commit" in guide
    # Existing dirty/foreign state left by someone else is still explained.
    assert "Commit existing scratch content" in guide
    assert "chronon commit <path>" in guide


def test_render_vault_section_honors_allow_scratch_too() -> None:
    with_scratch = render_vault_section("knowledge")
    without = render_vault_section("knowledge", allow_scratch=False)

    assert "Save scratch content" in with_scratch
    assert "Save scratch content" not in without
    # The vault-specific content itself is unaffected by the toggle.
    assert 'vault="knowledge"' in without
    assert 'vault="knowledge"' in with_scratch


def test_get_agent_instructions_returns_generic_text_without_a_vault() -> None:
    text = get_agent_instructions()

    assert text.startswith(BEGIN_MARKER)
    assert "Save scratch content" not in text
    assert "<selected-vault>" in text


def test_get_agent_instructions_specializes_and_validates_a_vault(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CHRONON_CONFIG_HOME", str(tmp_path / "config"))
    init_repository(tmp_path / "vault", register_vault="knowledge")

    text = get_agent_instructions(vault="knowledge")

    assert 'vault="knowledge"' in text
    assert "<selected-vault>" not in text

    with pytest.raises(InvalidArgument):
        get_agent_instructions(vault="missing")


def test_get_agent_instructions_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    get_agent_instructions()

    assert list(tmp_path.iterdir()) == []


def test_cli_agent_instructions_defaults_to_no_scratch(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["agent-instructions"])

    assert result.exit_code == 0, result.output
    assert "Save scratch content" not in result.output
    assert "Chronon" in result.output
    assert list(tmp_path.iterdir()) == []  # writes nothing


def test_cli_agent_instructions_allow_scratch_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["agent-instructions", "--allow-scratch"])

    assert result.exit_code == 0, result.output
    assert "Save scratch content" in result.output


def test_cli_agent_instructions_json(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["agent-instructions", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["allow_scratch"] is False
    assert "Chronon" in payload["instructions"]


def test_cli_agent_instructions_respects_vault_option(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHRONON_CONFIG_HOME", str(tmp_path / "config"))
    init_repository(tmp_path / "vault", register_vault="knowledge")

    result = runner.invoke(app, ["--vault", "knowledge", "agent-instructions"])

    assert result.exit_code == 0, result.output
    assert 'vault="knowledge"' in result.output
