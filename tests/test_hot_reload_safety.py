import asyncio
import importlib.util
import os
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.monotonic = Mock(side_effect=lambda: self.now)
        self.sleep = AsyncMock(side_effect=self.advance)

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def reload_env(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "node" / "app" / "hot_reload.py"
    spec = importlib.util.spec_from_file_location("hot_reload_safety", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    clock = FakeClock()
    execv = Mock()
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    to_thread = AsyncMock(side_effect=lambda func: func())
    monkeypatch.setattr(module, "os", SimpleNamespace(
        path=os.path, getcwd=lambda: "/synthetic/node",
        environ={"TEST_MARKER": "preserved"}, execv=execv,
    ))
    monkeypatch.setattr(module, "sys", SimpleNamespace(
        executable="/synthetic/python", orig_argv=["python", "-m", "uvicorn", "main:app"],
        argv=["uvicorn", "main:app"], stdout=Mock(), stderr=Mock(),
    ))
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=run))
    monkeypatch.setattr(module, "asyncio", SimpleNamespace(
        to_thread=to_thread, sleep=clock.sleep, wait_for=asyncio.wait_for,
    ))
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(module, "logging", SimpleNamespace(
        getLogger=Mock(return_value=SimpleNamespace(handlers=[Mock()])),
    ))

    registration = Mock()
    watchdog = Mock()
    client = Mock()
    client.stop = AsyncMock()
    manager = SimpleNamespace(
        _tunnel_locks={}, _io_lock=threading.Lock(), _watchdog_task=watchdog,
        stop_watchdog=Mock(side_effect=watchdog.cancel),
    )
    app = SimpleNamespace(state=SimpleNamespace(
        registration_task=registration, adapter_manager=manager, h2_client=client,
    ))
    return SimpleNamespace(
        module=module, clock=clock, execv=execv, run=run, to_thread=to_thread,
        app=app, manager=manager, registration=registration, watchdog=watchdog,
        client=client,
    )


def assert_background_untouched(env):
    assert env.app.state.registration_task is env.registration
    assert env.app.state.adapter_manager is env.manager
    assert env.manager._watchdog_task is env.watchdog
    assert env.app.state.h2_client is env.client
    assert env.registration.mock_calls == []
    assert env.watchdog.mock_calls == []
    env.manager.stop_watchdog.assert_not_called()
    assert env.client.mock_calls == []


@pytest.mark.parametrize("failure", ["stderr", "stdout", "spawn", "timeout"])
def test_invalid_import_aborts_without_touching_runtime(reload_env, caplog, failure):
    env = reload_env
    if failure in {"stderr", "stdout"}:
        env.run.return_value.returncode = 1
        setattr(env.run.return_value, failure, "ImportError: invalid new code")
    elif failure == "spawn":
        env.run.side_effect = OSError("cannot start validation")
    else:
        env.run.side_effect = subprocess.TimeoutExpired("synthetic validation", 60)

    asyncio.run(env.module._reload(env.app))

    env.to_thread.assert_awaited_once_with(env.module._validate_new_code)
    env.execv.assert_not_called()
    env.clock.monotonic.assert_not_called()
    env.clock.sleep.assert_not_called()
    assert "Hot reload aborted" in caplog.text
    assert env.module._reload_in_progress is False
    assert_background_untouched(env)


def test_validation_preserves_child_import_contract(reload_env):
    env = reload_env
    assert env.module._validate_new_code() is True
    env.run.assert_called_once_with(
        ["/synthetic/python", "-c", "import main"],
        cwd="/synthetic/node", capture_output=True, text=True, timeout=60,
        env={"TEST_MARKER": "preserved", "SMITE_RELOAD_VALIDATION": "1"},
    )
    assert env.module.os.environ == {"TEST_MARKER": "preserved"}


@pytest.mark.parametrize("kind", ["tunnel", "io", "both"])
@pytest.mark.parametrize("sleep_elapsed", [0.25, 20.0])
def test_busy_locks_abort_at_deadline(reload_env, caplog, kind, sleep_elapsed):
    env = reload_env

    async def scenario():
        tunnel_lock = asyncio.Lock()
        env.manager._tunnel_locks["active"] = tunnel_lock
        held = []
        if kind in {"tunnel", "both"}:
            await tunnel_lock.acquire()
            held.append(tunnel_lock)
        if kind in {"io", "both"}:
            env.manager._io_lock.acquire()
            held.append(env.manager._io_lock)
        env.clock.sleep.side_effect = lambda delay: env.clock.advance(sleep_elapsed)
        try:
            await env.module._reload(env.app)
            assert all(lock.locked() for lock in held)
        finally:
            for lock in held:
                lock.release()

    asyncio.run(scenario())

    env.execv.assert_not_called()
    assert env.clock.sleep.await_count == (60 if sleep_elapsed == 0.25 else 1)
    assert all(call.args == (0.25,) for call in env.clock.sleep.await_args_list)
    assert "still busy after 15 seconds" in caplog.text
    assert env.module._reload_in_progress is False
    assert_background_untouched(env)


@pytest.mark.parametrize("kind", ["tunnel", "io"])
@pytest.mark.parametrize("release_at", [0.25, 15.0])
def test_lock_released_during_wait_allows_exec(reload_env, kind, release_at):
    env = reload_env

    async def scenario():
        if kind == "tunnel":
            lock = asyncio.Lock()
            env.manager._tunnel_locks["active"] = lock
            await lock.acquire()
        else:
            lock = env.manager._io_lock
            lock.acquire()

        def sleep(delay):
            env.clock.advance(delay)
            if env.clock.now >= release_at:
                lock.release()

        env.clock.sleep.side_effect = sleep
        await env.module._reload(env.app)
        assert not lock.locked()

    asyncio.run(scenario())

    argv = ["/synthetic/python", "-m", "uvicorn", "main:app"]
    env.execv.assert_called_once_with(argv[0], argv)
    assert env.clock.now == release_at
    assert env.module._reload_in_progress is False
    assert_background_untouched(env)


def test_locks_are_refreshed_after_each_wait(reload_env):
    env = reload_env

    async def scenario():
        original, later = asyncio.Lock(), asyncio.Lock()
        await original.acquire()
        env.manager._tunnel_locks["original"] = original

        async def sleep(delay):
            env.clock.advance(delay)
            if env.clock.sleep.await_count == 1:
                original.release()
                env.manager._tunnel_locks.pop("original")
                await later.acquire()
                env.manager._tunnel_locks["later"] = later
            else:
                later.release()

        env.clock.sleep.side_effect = sleep
        await env.module._reload(env.app)
        assert not later.locked()

    asyncio.run(scenario())

    assert env.clock.sleep.await_count == 2
    env.execv.assert_called_once()
    assert_background_untouched(env)


def test_drain_deadline_starts_after_validation(reload_env):
    env = reload_env

    def validate(*args, **kwargs):
        env.clock.advance(60)
        env.manager._io_lock.acquire()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    env.run.side_effect = validate
    try:
        asyncio.run(env.module._reload(env.app))
        assert env.clock.now == 75
        assert env.clock.sleep.await_count == 60
        env.execv.assert_not_called()
    finally:
        env.manager._io_lock.release()
    assert env.module._reload_in_progress is False
    assert_background_untouched(env)


def test_exec_failure_leaves_background_untouched_and_allows_retry(reload_env, caplog):
    env = reload_env
    env.execv.side_effect = OSError("synthetic exec failure")

    asyncio.run(env.module._reload(env.app))

    assert "continuing with current code" in caplog.text
    assert env.module._reload_in_progress is False
    env.execv.assert_called_once()
    assert_background_untouched(env)

    env.execv.side_effect = None
    asyncio.run(env.module._reload(env.app))
    assert env.execv.call_count == 2
    assert env.module._reload_in_progress is False
    assert_background_untouched(env)


def test_final_drain_check_and_exec_do_not_yield(reload_env):
    env = reload_env

    async def scenario():
        events = []
        loop = asyncio.get_running_loop()

        class ObservedLock:
            def locked(self):
                loop.call_soon(events.append, "mutation")
                return False

        env.manager._tunnel_locks["observed"] = ObservedLock()
        env.execv.side_effect = lambda *args: events.append("exec")
        await env.module._reload(env.app)
        assert events == ["exec"]
        await asyncio.sleep(0)
        assert events == ["exec", "mutation"]

    asyncio.run(scenario())
    env.clock.sleep.assert_not_called()
    assert_background_untouched(env)


@pytest.mark.parametrize("phase", ["validation", "drain"])
def test_concurrent_reload_is_ignored(reload_env, phase):
    env = reload_env

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def validate(func):
            entered.set()
            await release.wait()
            return func()

        async def sleep(delay):
            entered.set()
            await release.wait()
            env.clock.advance(delay)
            env.manager._io_lock.release()

        if phase == "validation":
            env.to_thread.side_effect = validate
        else:
            env.manager._io_lock.acquire()
            env.clock.sleep.side_effect = sleep
        first = asyncio.create_task(env.module._reload(env.app))
        try:
            await entered.wait()
            assert env.module._reload_in_progress is True
            await env.module._reload(env.app)
            assert env.module._reload_in_progress is True
            env.to_thread.assert_awaited_once()
            env.execv.assert_not_called()
        finally:
            release.set()
            await first
        env.execv.assert_called_once()
        assert env.module._reload_in_progress is False

    asyncio.run(scenario())
    assert_background_untouched(env)


@pytest.mark.parametrize("phase", ["validation", "drain"])
def test_cancellation_releases_reload_guard(reload_env, phase):
    env = reload_env
    if phase == "validation":
        env.to_thread.side_effect = asyncio.CancelledError
    else:
        env.manager._io_lock.acquire()
        env.clock.sleep.side_effect = asyncio.CancelledError
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(env.module._reload(env.app))
    finally:
        if env.manager._io_lock.locked():
            env.manager._io_lock.release()
    env.execv.assert_not_called()
    assert env.module._reload_in_progress is False
    assert_background_untouched(env)


@pytest.mark.parametrize("missing", ["state", "manager", "locks", "io_lock"])
def test_optional_runtime_attributes(reload_env, missing):
    env = reload_env
    if missing == "state":
        app = SimpleNamespace()
    elif missing == "manager":
        app = SimpleNamespace(state=SimpleNamespace())
    else:
        app = env.app
        delattr(env.manager, "_tunnel_locks" if missing == "locks" else "_io_lock")

    asyncio.run(env.module._reload(app))

    env.execv.assert_called_once()
    env.clock.sleep.assert_not_called()
    assert env.module._reload_in_progress is False
    assert_background_untouched(env)


@pytest.mark.parametrize("orig,argv,expected", [
    (["python", "-I", "-m", "uvicorn", "main:app"], ["ignored"],
     ["-I", "-m", "uvicorn", "main:app"]),
    (["python", "/bin/uvicorn", "main:app"], ["ignored"],
     ["/bin/uvicorn", "main:app"]),
    (None, ["/packages/uvicorn/__main__.py", "main:app"],
     ["-m", "uvicorn", "main:app"]),
    ([], ["/packages/other/__main__.py", "arg"], ["-m", "other", "arg"]),
    (["python"], ["/bin/uvicorn", "main:app"], ["-m", "uvicorn", "main:app"]),
    (None, ["/bin/uvicorn.exe", "main:app"], ["-m", "uvicorn", "main:app"]),
    (None, ["/bin/custom-uvicorn", "main:app"], ["-m", "uvicorn", "main:app"]),
    (None, ["/script dir/agent.py", "arg with spaces"],
     ["/script dir/agent.py", "arg with spaces"]),
    (None, [], []),
])
def test_exec_argv_variants(reload_env, orig, argv, expected):
    module = reload_env.module
    if orig is None:
        del module.sys.orig_argv
    else:
        module.sys.orig_argv = list(orig)
    module.sys.argv = list(argv)

    assert module._build_exec_argv() == ["/synthetic/python", *expected]
    assert module.sys.argv == argv
    if orig is not None:
        assert module.sys.orig_argv == orig
