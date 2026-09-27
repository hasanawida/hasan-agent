from pathlib import Path

import pytest

from hassan_ai.config import POLICIES_DIR
from hassan_ai.llm import extract_json
from hassan_ai.policy import APPROVAL, AUTO, FORBIDDEN, Policy, PolicyError, resolve_in_workspace


@pytest.fixture
def policy():
    return Policy.load(POLICIES_DIR / "default.yaml")


def test_policy_buckets(policy):
    assert policy.decide("files.read") == AUTO
    assert policy.decide("verify.test") == AUTO
    assert policy.decide("files.write") == APPROVAL
    assert policy.decide("git.push") == APPROVAL
    assert policy.decide("shell.exec") == FORBIDDEN
    assert policy.decide("something.unknown") == APPROVAL


def test_mcp_verbs_classified(policy):
    assert policy.decide_mcp("visual-studio", "get_diagnostics") == AUTO
    assert policy.decide_mcp("blender", "list_objects") == AUTO
    assert policy.decide_mcp("blender", "execute_python") == APPROVAL
    assert policy.decide_mcp("visual-studio", "rename_symbol") == APPROVAL
    # exact per-tool entries override the generic mcp.*.* approval rule
    assert policy.decide_mcp("local-project", "git_status") == AUTO
    assert policy.decide_mcp("other", "git_status") == APPROVAL


def test_secret_files(policy):
    assert policy.is_secret(".env")
    assert policy.is_secret("config/id_rsa")
    assert not policy.is_secret("src/app.py")


def test_path_containment(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    assert resolve_in_workspace(tmp_path, "a.txt") == tmp_path / "a.txt"
    for bad in ("../outside.txt", "/etc/passwd", ".git/config"):
        with pytest.raises(PolicyError):
            resolve_in_workspace(tmp_path, bad)


def test_symlink_escape_blocked(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}_outside"
    outside.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    try:
        (ws / "link").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account lacks symlink privilege; covered on Linux CI")
        raise
    with pytest.raises(PolicyError):
        resolve_in_workspace(ws, "link/file.txt")


def test_extract_json_variants():
    assert extract_json('noise ```json\n{"a": 1}\n``` tail') == {"a": 1}
    assert extract_json('prefix {"a": {"b": 2}} suffix') == {"a": {"b": 2}}
    assert extract_json("no json here") is None
