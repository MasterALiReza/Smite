import importlib.util
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def panel_cli(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("panel_update_cli", ROOT / "cli/smite.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    (project / "cli").mkdir()
    (project / "cli" / "smite.py").write_text("# new cli\n")
    compose = project / "docker-compose.yml"
    compose.write_text("services: {}\n")
    monkeypatch.setattr(module, "get_compose_file", lambda: compose)

    state = {
        "ids": ["old-id", "new-id"],
        "git": 0,
        "health": True,
        "git_status": "",
        "checked_out": None,
    }
    compose_calls = []

    def fake_compose(args, **kwargs):
        compose_calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    def fake_run(command, **kwargs):
        if command[:3] == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(command, 0, state["git_status"], "")
        if command[:2] == ["git", "checkout"]:
            state["checked_out"] = command[2]
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:2] == ["git", "pull"]:
            return subprocess.CompletedProcess(command, state["git"], "", "diverged" if state["git"] else "")
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, "b" * 40 + "\n", "")
        if command[:3] == ["docker", "inspect", "-f"]:
            if not state["ids"]:
                return subprocess.CompletedProcess(command, 1, "", "no such container")
            current = state["ids"][0] if len(state["ids"]) == 1 else state["ids"].pop(0)
            return subprocess.CompletedProcess(command, 0, f"{current} true\n", "")
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(module, "run_docker_compose", fake_compose)
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.shutil, "copy2", Mock())
    monkeypatch.setattr(module.os, "chmod", Mock())
    real_wait = module._wait_for_new_panel
    monkeypatch.setattr(module, "_wait_for_new_panel", lambda previous: "new-id" if state["health"] else None)
    return module, project, state, compose_calls, real_wait


def test_update_recreates_only_the_panel_service(panel_cli):
    module, project, state, calls, _ = panel_cli
    module.cmd_update(Mock())

    assert ["up", "-d", "--no-deps", "--force-recreate", "--build", "smite-panel"] in calls
    flat = [str(part) for call in calls for part in call]
    assert "nginx" not in flat
    assert all("smite-panel" in call or call[:1] == ["pull"] for call in calls)
    module.shutil.copy2.assert_called_once()


def test_dirty_git_aborts_update_before_pull(panel_cli):
    module, project, state, calls, _ = panel_cli
    state["git_status"] = " M panel/app/spec_builder.py\n"
    with pytest.raises(SystemExit) as error:
        module.cmd_update(Mock())
    assert error.value.code == 1
    assert calls == []
    module.shutil.copy2.assert_not_called()


def test_git_pull_failure_aborts_before_any_service_change(panel_cli):
    module, project, state, calls, _ = panel_cli
    state["git"] = 1
    with pytest.raises(SystemExit) as error:
        module.cmd_update(Mock())
    assert error.value.code == 1
    assert calls == []
    module.shutil.copy2.assert_not_called()


def test_update_without_git_still_scopes_the_service(panel_cli):
    module, project, state, calls, _ = panel_cli
    import shutil
    shutil.rmtree(project / ".git")
    module.cmd_update(Mock())
    assert ["up", "-d", "--no-deps", "--force-recreate", "--build", "smite-panel"] in calls


def test_image_pull_failure_does_not_abort_the_local_build(panel_cli, monkeypatch):
    module, project, state, calls, _ = panel_cli

    def flaky(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 1 if args[0] == "pull" else 0, "", "registry error")

    monkeypatch.setattr(module, "run_docker_compose", flaky)
    module.cmd_update(Mock())
    assert any(call[0] == "pull" and call[1] == "smite-panel" for call in calls)
    assert any(call[0] == "up" and call[-1] == "smite-panel" for call in calls)


def test_unhealthy_panel_triggers_rollback_and_exits(panel_cli):
    module, project, state, calls, _ = panel_cli
    state["health"] = False
    with pytest.raises(SystemExit) as error:
        module.cmd_update(Mock())
    assert error.value.code == 1
    # Check that git rollback to previous commit occurred
    assert state["checked_out"] == "b" * 40
    # Check that recreate was called again for rollback
    up_calls = [c for c in calls if c[:2] == ["up", "-d"]]
    assert len(up_calls) == 2  # initial attempt + rollback attempt
    module.shutil.copy2.assert_not_called()


def test_unchanged_container_is_an_activation_failure_and_rolls_back(panel_cli, monkeypatch):
    module, project, state, calls, _ = panel_cli
    state["ids"] = ["same-id"]
    with pytest.raises(SystemExit) as error:
        module.cmd_update(Mock())
    assert error.value.code == 1
    assert state["checked_out"] == "b" * 40
    module.shutil.copy2.assert_not_called()


def test_wait_for_new_panel_requires_a_new_running_container(panel_cli, monkeypatch):
    module, project, state, calls, real_wait = panel_cli
    monkeypatch.setattr(module, "_wait_for_new_panel", real_wait)
    state["ids"] = ["old-id"]
    monkeypatch.setattr(module, "_panel_container_id", lambda: "old-id")
    monkeypatch.setattr(module, "_panel_health_ok", lambda: True)
    ticks = {"now": 0.0}

    def monotonic():
        ticks["now"] += 10
        return ticks["now"]

    monkeypatch.setattr("time.monotonic", monotonic)
    monkeypatch.setattr("time.sleep", lambda *a: None)
    assert module._wait_for_new_panel("old-id", timeout=30) is None

    monkeypatch.setattr(module, "_panel_container_id", lambda: "new-id")
    ticks["now"] = 0.0
    assert module._wait_for_new_panel("old-id", timeout=30) == "new-id"


def test_db_backup_and_prune_keeping_last_10(tmp_path):
    spec = importlib.util.spec_from_file_location("panel_update_cli", ROOT / "cli/smite.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    data_dir = tmp_path / "panel" / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "smite.db"
    db_file.write_text("sqlite db content")

    # Create 12 pre-existing backup files with older mtimes
    import time
    for i in range(12):
        old_bak = data_dir / f"smite.db.bak_auto_{20260101000000 + i}"
        old_bak.write_text(f"backup {i}")
        # stagger modification time
        os.utime(old_bak, (time.time() - (100 - i) * 10, time.time() - (100 - i) * 10))

    created = module._backup_panel_db(tmp_path, max_backups=10)
    assert created is not None
    assert created.exists()

    remaining_backups = list(data_dir.glob("smite.db.bak_auto_*"))
    assert len(remaining_backups) == 10


def test_update_rollback_restores_database(panel_cli, monkeypatch):
    module, project, state, calls, _ = panel_cli
    data_dir = project / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "smite.db"
    db_file.write_text("original db content")

    state["health"] = False

    copies = []
    def recording_copy(src, dst):
        Path(dst).write_bytes(Path(src).read_bytes())
        copies.append((Path(src), Path(dst)))
        return dst

    monkeypatch.setattr(module.shutil, "copy2", recording_copy)

    with pytest.raises(SystemExit) as error:
        module.cmd_update(Mock())
    assert error.value.code == 1

    # Should have backed up smite.db first, and then restored it on rollback!
    assert len(copies) == 2
    # 1st copy: backup (db_file -> backup_file)
    assert copies[0][0] == db_file
    assert "smite.db.bak_auto_" in str(copies[0][1])
    # 2nd copy: restore (backup_file -> db_file)
    assert copies[1][0] == copies[0][1]
    assert copies[1][1] == db_file


