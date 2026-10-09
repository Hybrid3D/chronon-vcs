import asyncio
from pathlib import Path

import pytest

from chronon.api.operations import ChrononRepository, init_repository
from chronon.core import vaults
from chronon.core.docs import ADMIN_AGENT_RULE
from chronon.mcp_server import mcp


def test_mcp_exposes_manual_versioning_tools() -> None:
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {
        "initialize_repository",
        "add_resource",
        "move_resource",
        "copy_resource",
        "create_resource",
        "put_resource",
        "commit_resource",
        "diff_resource",
        "history_resource",
        "list_directory",
        "register_schema",
        "write_agent_instructions",
        "get_agent_instructions",
    } <= names
    assert "lock_resource" not in names
    assert not any("admin" in name or "vault_path" in name for name in names)


def test_mcp_server_instructions_match_safe_tool_first_workflow() -> None:
    instructions = mcp.instructions or ""

    assert instructions.startswith(f"Rule 1: {ADMIN_AGENT_RULE}")
    assert "Use these MCP tools" in instructions
    assert "not direct filesystem writes or the Chronon CLI" in instructions
    assert "working_revision" in instructions
    assert "expected_revision" in instructions
    assert "revision_conflict" in instructions
    assert "with an error field" in instructions
    # The tool that returns the full text guide must be self-announcing: an
    # MCP client sees this string on connect, before it ever calls a tool.
    assert "get_agent_instructions" in instructions


def test_mcp_lists_vault_names_without_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CHRONON_CONFIG_HOME", str(tmp_path / "config"))
    init_repository(tmp_path / "private storage", register_vault="mine")

    _, result = asyncio.run(mcp.call_tool("list_vaults", {}))

    assert result == {"vaults": [{"name": "mine"}]}


def test_mcp_tool_calls_shared_operations(tmp_path: Path, monkeypatch) -> None:
    init_repository(tmp_path)
    (tmp_path / "docs.yml").write_text("value: 1\n", encoding="utf-8")
    repository = ChrononRepository(tmp_path)
    repository.add("docs.yml")
    working_revision = repository.read("docs.yml")["working_revision"]
    monkeypatch.chdir(tmp_path)

    _, result = asyncio.run(
        mcp.call_tool(
            "commit_resource",
            {
                "resource": "docs.yml",
                "message": "initial",
                "expected_revision": working_revision,
            },
        )
    )
    assert result["committed"] is True
    assert result["commit"]["seq"] == 1


@pytest.mark.parametrize("tool_name", ["add_vault", "initialize_repository"])
def test_mcp_registration_cannot_change_an_existing_vault_path(
    tmp_path: Path, monkeypatch, tool_name: str
) -> None:
    monkeypatch.setenv("CHRONON_CONFIG_HOME", str(tmp_path / "config"))
    old_root, new_root = tmp_path / "old", tmp_path / "new"
    init_repository(old_root, register_vault="mine")
    init_repository(new_root)
    before = vaults.registry_path().read_bytes()
    arguments = (
        {"name": "mine", "path": str(new_root)}
        if tool_name == "add_vault"
        else {"directory": str(new_root), "register_vault": "mine"}
    )

    _, result = asyncio.run(mcp.call_tool(tool_name, arguments))

    assert result["error"] == "invalid_argument"
    assert "human administrator" in result["hint"]
    assert str(old_root) not in str(result)
    assert vaults.registry_path().read_bytes() == before


def test_mcp_returns_structured_domain_errors(tmp_path: Path, monkeypatch) -> None:
    init_repository(tmp_path)
    monkeypatch.chdir(tmp_path)
    _, result = asyncio.run(
        mcp.call_tool("status_resource", {"resource": "missing.yml"})
    )
    assert result["error"] == "resource_not_tracked"


def test_mcp_can_initialize_and_create_a_resource(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "vault"
    monkeypatch.chdir(tmp_path)

    _, initialized = asyncio.run(
        mcp.call_tool(
            "initialize_repository",
            {
                "directory": str(root),
            },
        )
    )
    assert initialized["created"] is True
    assert not (root / "CHRONON.md").exists()

    monkeypatch.chdir(root)
    _, created = asyncio.run(
        mcp.call_tool(
            "create_resource",
            {
                "resource": "notes.yml",
                "content": "value: 1\n",
                "message": "initial",
            },
        )
    )
    assert created["created"] is True
    assert created["committed"] is True

    _, listing = asyncio.run(mcp.call_tool("list_directory", {"include_status": True}))
    assert listing["entries"][0]["state"] == "clean"


def test_mcp_agent_instructions_error_is_structured(
    tmp_path: Path, monkeypatch
) -> None:
    init_repository(tmp_path)
    monkeypatch.chdir(tmp_path)
    _, result = asyncio.run(
        mcp.call_tool("write_agent_instructions", {"directory": "missing-directory"})
    )
    assert result["error"] == "invalid_argument"


def test_mcp_get_agent_instructions_returns_plain_text_guidance() -> None:
    _, result = asyncio.run(mcp.call_tool("get_agent_instructions", {}))

    assert "Chronon" in result["instructions"]
    assert "read_resource" in result["instructions"]
    assert "chronon read <path>" in result["instructions"]
    # Scratch writes are left out of the default guidance.
    assert "Save scratch content" not in result["instructions"]
    assert "chronon --stdin --scratch" not in result["instructions"]


def test_mcp_get_agent_instructions_can_include_scratch() -> None:
    _, result = asyncio.run(
        mcp.call_tool("get_agent_instructions", {"allow_scratch": True})
    )

    assert "Save scratch content" in result["instructions"]
    assert "--stdin --scratch" in result["instructions"]


def test_mcp_get_agent_instructions_specializes_for_a_vault(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CHRONON_CONFIG_HOME", str(tmp_path / "config"))
    init_repository(tmp_path / "vault", register_vault="knowledge")

    _, result = asyncio.run(
        mcp.call_tool("get_agent_instructions", {"vault": "knowledge"})
    )

    assert 'vault="knowledge"' in result["instructions"]


def test_mcp_get_agent_instructions_rejects_an_unknown_vault(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CHRONON_CONFIG_HOME", str(tmp_path / "config"))

    _, result = asyncio.run(
        mcp.call_tool("get_agent_instructions", {"vault": "missing"})
    )

    assert result["error"] == "invalid_argument"


def test_mcp_write_resource_cannot_create_and_points_to_create_resource(
    tmp_path: Path, monkeypatch
) -> None:
    init_repository(tmp_path)
    monkeypatch.chdir(tmp_path)

    _, result = asyncio.run(
        mcp.call_tool("write_resource", {"resource": "new.md", "content": "x\n"})
    )

    assert result["error"] == "resource_not_tracked"
    assert "create_resource" in result["hint"]
    assert not (tmp_path / "new.md").exists()


def test_mcp_create_resource_rejects_existing_resource(
    tmp_path: Path, monkeypatch
) -> None:
    init_repository(tmp_path)
    monkeypatch.chdir(tmp_path)
    args = {"resource": "a.md", "content": "one\n", "message": "first"}
    asyncio.run(mcp.call_tool("create_resource", args))

    _, again = asyncio.run(mcp.call_tool("create_resource", args))

    assert again["error"] == "resource_already_tracked"
    assert "write_resource" in again["hint"]
