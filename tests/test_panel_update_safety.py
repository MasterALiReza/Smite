import importlib.util
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

    state = {"ids": ["old-id", "new-id"], "git": 0, "health": True}
    compose_calls = []

    def fake_compose(args, **kwargs):
        compose_calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    def fake_run(command, **kwargs):
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


def test_unhealthy_panel_exits_without_updating_cli(panel_cli):
    module, project, state, calls, _ = panel_cli
    state["health"] = False
    with pytest.raises(SystemExit) as error:
        module.cmd_update(Mock())
    assert error.value.code == 1
    module.shutil.copy2.assert_not_called()


def test_unchanged_container_is_an_activation_failure(panel_cli, monkeypatch):
    module, project, state, calls, _ = panel_cli
    state["ids"] = ["same-id"]
    with pytest.raises(SystemExit) as error:
        module.cmd_update(Mock())
    assert error.value.code == 1
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
