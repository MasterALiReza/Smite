import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40


@pytest.fixture
def cli():
    spec = importlib.util.spec_from_file_location("node_update_cli", ROOT / "cli/smite-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def update(cli, tmp_path, monkeypatch):
    root = tmp_path / "installation"
    (root / "app").mkdir(parents=True)
    (root / "main.py").write_bytes(b"old = True\n")
    node = {"dir": root, "container_name": "test-node", "port": 18888}
    baseline = {"status": "ok", "agent_started_at": 100, "tunnels": ["test-tunnel"], "hot_reload": True}
    state = {"status": baseline}
    monkeypatch.setattr(cli, "discover_nodes", lambda: [node])
    monkeypatch.setattr(cli, "select_target_node", lambda nodes, *a, **kw: nodes)
    monkeypatch.setattr(cli, "_agent_status", lambda *a, **kw: state["status"])
    real_reload_is_safe = cli._reload_is_safe
    monkeypatch.setattr(cli, "_reload_is_safe", lambda *a: True)
    monkeypatch.setattr(cli, "_resolve_revision", Mock(return_value=REVISION))
    monkeypatch.setattr(cli, "_download_node_code", Mock(return_value={name: b"value = 1\n" for name in cli.NODE_CODE_FILES}))
    monkeypatch.setattr(cli, "_fetch", Mock(return_value=b"pass\n"))
    monkeypatch.setattr(cli, "_validate_runtime", Mock())
    monkeypatch.setattr(cli, "_self_update_cli", Mock())
    monkeypatch.setattr(cli, "apply_low_latency_profile", Mock(side_effect=AssertionError("Host tuning is forbidden")))
    monkeypatch.setattr(cli.subprocess, "run", Mock(side_effect=AssertionError("Unexpected subprocess")))
    monkeypatch.setattr(cli, "_restart_and_wait", Mock(side_effect=AssertionError("Unexpected restart")))

    def reload(node, expected_release=None):
        state["status"] = dict(baseline, agent_started_at=101, release=expected_release)
        return True

    monkeypatch.setattr(cli, "_hot_reload", Mock(side_effect=reload))
    args = SimpleNamespace(target="test-node", repo=None, branch=None, revision=None,
                           image=False, yes=False, no_tune=False, allow_restart=False)
    return SimpleNamespace(cli=cli, root=root, node=node, args=args, baseline=baseline,
                           state=state, real_reload_is_safe=real_reload_is_safe)


def test_update_retains_host_and_uses_one_release(update):
    u = update
    u.cli.cmd_update(u.args)
    u.cli._download_node_code.assert_called_once_with(u.cli.DEFAULT_REPO, REVISION)
    u.cli._fetch.assert_called_once_with(f"https://raw.githubusercontent.com/{u.cli.DEFAULT_REPO}/{REVISION}/cli/smite-node.py")
    u.cli._self_update_cli.assert_called_once_with(b"pass\n")
    u.cli.apply_low_latency_profile.assert_not_called()
    u.cli._restart_and_wait.assert_not_called()
    manifest = json.loads((u.root / u.cli.RELEASE_MANIFEST).read_bytes())
    assert manifest["revision"] == REVISION
    assert set(manifest["files"]) == set(u.cli.NODE_CODE_FILES)
    assert not (u.root / u.cli.UPDATE_JOURNAL).exists()


@pytest.mark.parametrize("yes", [False, True])
def test_yes_cannot_authorize_implicit_restart(update, monkeypatch, yes):
    u = update
    u.args.yes = yes
    monkeypatch.setattr(u.cli, "_reload_is_safe", lambda *a: False)
    with pytest.raises(SystemExit, match="allow-restart"):
        u.cli.cmd_update(u.args)
    assert (u.root / "main.py").read_bytes() == b"old = True\n"
    u.cli._download_node_code.assert_not_called()


def test_declined_restart_does_not_download_or_write(update, monkeypatch):
    u = update
    u.args.allow_restart = True
    monkeypatch.setattr(u.cli, "_reload_is_safe", lambda *a: False)
    monkeypatch.setattr(u.cli, "_confirm", lambda *a: False)
    with pytest.raises(SystemExit, match="cancelled"):
        u.cli.cmd_update(u.args)
    assert (u.root / "main.py").read_bytes() == b"old = True\n"
    assert list(u.root.glob(".backup-*")) == []
    u.cli._download_node_code.assert_not_called()


def test_explicit_restart_is_verified_without_fallback(update, monkeypatch):
    u = update
    u.args.allow_restart = True
    u.args.yes = True
    monkeypatch.setattr(u.cli, "_reload_is_safe", lambda *a: False)
    monkeypatch.setattr(u.cli, "_restart_and_wait", Mock(side_effect=u.cli._hot_reload.side_effect))
    u.cli.cmd_update(u.args)
    u.cli._restart_and_wait.assert_called_once()
    u.cli._hot_reload.assert_not_called()


@pytest.mark.parametrize("failure", ["download", "runtime", "cli"])
def test_preflight_failure_leaves_active_files_untouched(update, failure):
    u = update
    target = {"download": u.cli._download_node_code, "runtime": u.cli._validate_runtime, "cli": u.cli._fetch}[failure]
    target.side_effect = RuntimeError("synthetic failure")
    with pytest.raises(SystemExit) as exc:
        u.cli.cmd_update(u.args)
    assert exc.value.code == 1
    assert (u.root / "main.py").read_bytes() == b"old = True\n"
    assert not (u.root / u.cli.UPDATE_JOURNAL).exists()
    u.cli._hot_reload.assert_not_called()
    u.cli._self_update_cli.assert_not_called()


def test_unknown_reload_outcome_keeps_recovery_journal_and_does_not_restart(update):
    u = update
    u.cli._hot_reload.side_effect = None
    u.cli._hot_reload.return_value = False
    with pytest.raises(SystemExit) as exc:
        u.cli.cmd_update(u.args)
    assert exc.value.code == 1
    assert json.loads((u.root / u.cli.UPDATE_JOURNAL).read_bytes())["phase"] == "awaiting_verification"
    u.cli._restart_and_wait.assert_not_called()
    u.cli._self_update_cli.assert_not_called()
    u.cli.subprocess.run.assert_not_called()


def test_tunnel_inventory_mismatch_blocks_success(update):
    u = update

    def reload(node, expected_release=None):
        u.state["status"] = dict(u.baseline, agent_started_at=101, release=expected_release, tunnels=[])
        return True

    u.cli._hot_reload.side_effect = reload
    with pytest.raises(SystemExit):
        u.cli.cmd_update(u.args)
    assert (u.root / u.cli.UPDATE_JOURNAL).exists()
    u.cli._self_update_cli.assert_not_called()


def test_incomplete_update_blocks_new_activation(update):
    u = update
    (u.root / u.cli.UPDATE_JOURNAL).write_text("{}")
    with pytest.raises(SystemExit, match="unfinished"):
        u.cli.cmd_update(u.args)
    u.cli._download_node_code.assert_not_called()


@pytest.mark.parametrize("kind", ["all", "image", "unreachable"])
def test_unsupported_operation_has_no_update_side_effects(update, monkeypatch, kind):
    u = update
    if kind == "all":
        monkeypatch.setattr(u.cli, "discover_nodes", lambda: [u.node, dict(u.node)])
    elif kind == "image":
        u.args.image = True
    else:
        u.state["status"] = None
    with pytest.raises(SystemExit):
        u.cli.cmd_update(u.args)
    u.cli._download_node_code.assert_not_called()
    u.cli.subprocess.run.assert_not_called()


def test_branch_is_resolved_once(cli, monkeypatch):
    fetch = Mock(return_value=json.dumps({"sha": REVISION}).encode())
    monkeypatch.setattr(cli, "_fetch", fetch)
    assert cli._resolve_revision("owner/project", "release/bridge") == REVISION
    fetch.assert_called_once_with("https://api.github.com/repos/owner/project/commits/release%2Fbridge")
    fetch.reset_mock()
    assert cli._resolve_revision("owner/project", REVISION.upper()) == REVISION
    fetch.assert_not_called()


@pytest.mark.parametrize("repo", ["../repo", "owner/repo?token=x", "owner/repo/extra", "https://example.com"])
def test_repository_is_validated(cli, repo):
    with pytest.raises(ValueError):
        cli._resolve_revision(repo, REVISION)


def test_download_ref_must_be_immutable(cli, monkeypatch):
    fetch = Mock()
    monkeypatch.setattr(cli, "_fetch", fetch)
    with pytest.raises(ValueError):
        cli._download_node_code("owner/project", "main")
    fetch.assert_not_called()


def test_backup_restores_all_files_and_main_inode(cli, tmp_path):
    (tmp_path / "main.py").write_bytes(b"original\n")
    inode = (tmp_path / "main.py").stat().st_ino
    files = {"main.py": b"updated\n", "app/hot_reload.py": b"new module\n", cli.RELEASE_MANIFEST: b"{}"}
    backup = cli._write_code_with_backup(tmp_path, files)
    assert (tmp_path / "main.py").stat().st_ino == inode
    cli._restore_backup(tmp_path, backup)
    assert (tmp_path / "main.py").read_bytes() == b"original\n"
    assert (tmp_path / "main.py").stat().st_ino == inode
    assert not (tmp_path / "app/hot_reload.py").exists()
    assert not (tmp_path / cli.RELEASE_MANIFEST).exists()
    assert not (tmp_path / cli.UPDATE_JOURNAL).exists()


def test_partial_write_failure_rolls_back_without_service_actions(cli, tmp_path, monkeypatch):
    (tmp_path / "main.py").write_bytes(b"original\n")
    write = cli._write_file
    failed = False

    def interrupted(target, content, **kwargs):
        nonlocal failed
        if target == tmp_path / "app/hot_reload.py" and not failed:
            failed = True
            raise OSError("synthetic disk error")
        return write(target, content, **kwargs)

    monkeypatch.setattr(cli, "_write_file", interrupted)
    with pytest.raises(OSError, match="disk error"):
        cli._write_code_with_backup(tmp_path, {"main.py": b"updated\n", "app/hot_reload.py": b"new\n"})
    assert (tmp_path / "main.py").read_bytes() == b"original\n"
    assert not (tmp_path / "app/hot_reload.py").exists()
    assert not (tmp_path / cli.UPDATE_JOURNAL).exists()


def test_bad_backup_is_rejected_before_any_restore(cli, tmp_path):
    (tmp_path / "main.py").write_bytes(b"original\n")
    backup = cli._write_code_with_backup(tmp_path, {"main.py": b"updated\n", "app/hot_reload.py": b"new\n"})
    (backup / "main.py").write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="checksum"):
        cli._restore_backup(tmp_path, backup)
    assert (tmp_path / "main.py").read_bytes() == b"updated\n"
    assert (tmp_path / "app/hot_reload.py").exists()
    assert (tmp_path / cli.UPDATE_JOURNAL).exists()


@pytest.mark.parametrize("name", ["../outside.py", "/tmp/outside.py", "app/../../outside.py", "app/not-agent.py"])
def test_activation_rejects_paths_outside_manifest(cli, tmp_path, name):
    with pytest.raises(ValueError):
        cli._write_code_with_backup(tmp_path, {name: b"pass"})
    assert list(tmp_path.iterdir()) == []


def test_journal_excludes_concurrent_activation(cli, tmp_path):
    cli._write_code_with_backup(tmp_path, {"main.py": b"first\n"})
    with pytest.raises(FileExistsError):
        cli._write_code_with_backup(tmp_path, {"main.py": b"second\n"})
    assert (tmp_path / "main.py").read_bytes() == b"first\n"


@pytest.mark.parametrize("docker_status,returncode", [("running", 0), ("paused", 0), ("restarting", 0), ("", 1)])
def test_recovery_never_stops_or_mutates_live_or_unknown_container(update, docker_status, returncode):
    u = update
    u.cli.subprocess.run.side_effect = None
    u.cli.subprocess.run.return_value = SimpleNamespace(returncode=returncode, stdout=docker_status)
    with pytest.raises(SystemExit, match="confirmed stopped"):
        u.cli.cmd_recover_update(u.args)
    assert (u.root / "main.py").read_bytes() == b"old = True\n"
    u.cli.subprocess.run.assert_called_once()


def test_stopped_container_recovery_restores_only_code(update):
    u = update
    u.cli._write_code_with_backup(u.root, {"main.py": b"updated\n", "app/hot_reload.py": b"new\n"})
    u.cli.subprocess.run.side_effect = None
    u.cli.subprocess.run.return_value = SimpleNamespace(returncode=0, stdout="exited\n")
    u.cli.cmd_recover_update(u.args)
    assert (u.root / "main.py").read_bytes() == b"old = True\n"
    assert not (u.root / "app/hot_reload.py").exists()
    u.cli.subprocess.run.assert_called_once()


def test_wait_rejects_changed_timestamp_with_wrong_release(cli, monkeypatch):
    expected = {"revision": REVISION, "manifest_sha256": "b" * 64}
    responses = iter([
        {"status": "ok", "agent_started_at": 101, "release": {"revision": "wrong"}},
        {"status": "ok", "agent_started_at": 101, "release": expected},
    ])
    monkeypatch.setattr(cli, "_agent_status", lambda *a: next(responses))
    monkeypatch.setattr("time.sleep", lambda *a: None)
    assert cli._wait_for_agent(18888, 100, expected_release=expected)["release"] == expected


@pytest.mark.parametrize("status,expected", [
    (None, False),
    ({}, False),
    ({"hot_reload": True}, False),
    ({"hot_reload": True, "capabilities": ["safe_reexec"]}, False),
    ({"hot_reload": True, "capabilities": ["drained_reexec"]}, False),
    ({"hot_reload": False, "capabilities": ["safe_reexec", "drained_reexec"]}, False),
    ({"hot_reload": True, "capabilities": ["safe_reexec", "drained_reexec"]}, True),
    ({"hot_reload": True, "capabilities": ["other", "safe_reexec", "drained_reexec"]}, True),
])
def test_reload_is_safe_requires_drain_capability(cli, status, expected):
    assert cli._reload_is_safe(status) is expected


def test_old_agent_force_requires_explicit_restart_option(update, monkeypatch):
    u = update
    monkeypatch.setattr(u.cli, "_reload_is_safe", u.real_reload_is_safe)
    u.state["status"] = dict(u.baseline, capabilities=["safe_reexec"])
    with pytest.raises(SystemExit) as error:
        u.cli.cmd_update(u.args)
    assert "--allow-restart" in str(error.value)
    u.cli._resolve_revision.assert_not_called()
    u.cli._download_node_code.assert_not_called()
    assert not (u.root / u.cli.UPDATE_JOURNAL).exists()


def test_entrypoint_is_replaced_after_modules(cli, tmp_path, monkeypatch):
    root = tmp_path / "installation"
    (root / "app").mkdir(parents=True)
    (root / "main.py").write_bytes(b"old = True\n")
    written = []
    real = cli._write_file

    def track(target, content, in_place=False):
        written.append(Path(target).resolve().as_posix())
        return real(target, content, in_place=in_place)

    monkeypatch.setattr(cli, "_write_file", track)
    files = {name: b"value = 1\n" for name in cli.NODE_CODE_FILES}
    files[cli.RELEASE_MANIFEST] = b"{}\n"
    cli._write_code_with_backup(root, files)

    prefix = root.resolve().as_posix() + "/"
    activation = [path[len(prefix):] for path in written if path.startswith(prefix) and path[len(prefix):] in files]
    assert activation[-1] == "main.py"
    assert activation[-2] == cli.RELEASE_MANIFEST


def test_restart_and_reload_refuse_unfinished_update(cli, tmp_path, monkeypatch):
    root = tmp_path / "installation"
    (root / "app").mkdir(parents=True)
    (root / cli.UPDATE_JOURNAL).write_text("{}")
    node = {"dir": root, "container_name": "test-node", "port": 18888, "id_str": "1",
            "name": "node-1", "index": 1, "role": "iran", "docker_status": "running",
            "api_status": "healthy", "active_tunnels": 0}
    monkeypatch.setattr(cli, "discover_nodes", lambda: [node])
    monkeypatch.setattr(cli, "select_target_node", lambda nodes, *a, **kw: nodes)
    monkeypatch.setattr(cli.subprocess, "run", Mock(side_effect=AssertionError("no side effects")))
    monkeypatch.setattr(cli, "run_compose_for_node", Mock(side_effect=AssertionError("no side effects")))

    with pytest.raises(SystemExit):
        cli.cmd_restart(SimpleNamespace(target="1"))
    with pytest.raises(SystemExit):
        cli.cmd_reload(SimpleNamespace(target="1"))
    cli.run_compose_for_node.assert_not_called()
    cli.subprocess.run.assert_not_called()


@pytest.mark.parametrize("valid", ["complete", "missing_drain", "broken"])
def test_runtime_preflight_imports_only_staged_code(cli, tmp_path, monkeypatch, valid):
    run = subprocess.run
    artifact = (b"from types import SimpleNamespace\n"
                b"agent = SimpleNamespace(AGENT_CAPABILITIES=['release_identity', 'drained_reexec'])\n")
    if valid == "missing_drain":
        artifact = b"from types import SimpleNamespace\nagent = SimpleNamespace(AGENT_CAPABILITIES=['release_identity'])\n"
    elif valid == "broken":
        artifact = b"raise RuntimeError('synthetic dependency failure')\n"

    def isolated_exec(command, **kwargs):
        assert command[:5] == ["docker", "exec", "-i", "fake-node", "python"]
        return run([sys.executable, "-I", "-B", *command[5:]], cwd=tmp_path, **kwargs)

    monkeypatch.setattr(cli.subprocess, "run", isolated_exec)
    if valid == "complete":
        cli._validate_runtime({"container_name": "fake-node"}, {"main.py": artifact})
    else:
        with pytest.raises(RuntimeError, match="Staged agent import failed"):
            cli._validate_runtime({"container_name": "fake-node"}, {"main.py": artifact})
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def release_reader(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT))
    monkeypatch.chdir(tmp_path)
    from node.app.routers import agent
    return agent._read_agent_release


def test_release_identity_requires_matching_complete_sources(cli, release_reader, tmp_path):
    files = {"main.py": b"pass\n", "app/__init__.py": b"", "app/routers/agent.py": b"pass\n"}
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    manifest = json.dumps(cli._release_manifest(REVISION, files), sort_keys=True).encode()
    (tmp_path / cli.RELEASE_MANIFEST).write_bytes(manifest)
    assert release_reader(tmp_path) == {"revision": REVISION, "manifest_sha256": hashlib.sha256(manifest).hexdigest()}
    (tmp_path / "main.py").write_bytes(b"modified\n")
    assert release_reader(tmp_path) is None
    (tmp_path / "main.py").write_bytes(files["main.py"])
    (tmp_path / "app/unlisted.py").write_bytes(b"pass\n")
    assert release_reader(tmp_path) is None


@pytest.mark.parametrize("content", [b"bad json", b"[]", b"{}", b'{"schema":1,"revision":null}', b'{"schema":2}'])
def test_bad_release_metadata_is_not_reported_as_verified(cli, release_reader, tmp_path, content):
    (tmp_path / "app").mkdir()
    (tmp_path / cli.RELEASE_MANIFEST).write_bytes(content)
    assert release_reader(tmp_path) is None
