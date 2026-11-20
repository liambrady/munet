# SPDX-License-Identifier: GPL-2.0-or-later
"""Cleanup order and coordination with independent process waiters."""

import asyncio
import signal
from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest

from munet.base import BaseMunet
from munet.lifecycle import LifecycleStage
from munet.native import Munet


async def test_reverse_cleanup_continues_after_failed_hook(make_node, tmp_path, caplog):
    order = tmp_path / "order"
    node = make_node(
        "r1",
        [
            LifecycleStage(cleanup_cmd=f"echo first >> {order}"),
            LifecycleStage(shell=False, cleanup_cmd="/no-such-command"),
            LifecycleStage(
                shell=False,
                cmd="/also-missing",
                cleanup_cmd=f"/usr/bin/touch {tmp_path / 'failed-cleanup'}",
            ),
            LifecycleStage(cleanup_cmd=f"echo never >> {order}"),
        ],
    )
    with pytest.raises(FileNotFoundError):
        await node.run_cmd()
    await node.async_cleanup_cmd()
    await node.async_cleanup_cmd()
    assert (tmp_path / "failed-cleanup").exists()
    assert order.read_text() == "first\n"
    assert "stage 2 cleanup failed" in caplog.text


async def test_overlapping_cleanup_and_finalization_are_once(make_node, monkeypatch):
    node = make_node(
        "r1",
        [
            LifecycleStage(cmd="exec sleep 60", cleanup_cmd="cleanup-first"),
            LifecycleStage(cleanup_cmd="cleanup-second"),
        ],
    )
    await node.run_cmd()
    entered_cleanup = asyncio.Event()
    finish_cleanup = asyncio.Event()
    cleanups = []

    async def run_hook(state, hook):
        assert hook == "cleanup-cmd"
        assert node.cmd_procs[0].proc.returncode is None
        cleanups.append(state.number)
        entered_cleanup.set()
        await finish_cleanup.wait()
        return 0

    cleanup_proc = AsyncMock(wraps=node.async_cleanup_proc)
    monkeypatch.setattr(node, "_run_stage_hook", run_hook)
    monkeypatch.setattr(node, "async_cleanup_proc", cleanup_proc)
    caller = asyncio.create_task(node.async_cleanup_cmd())
    await asyncio.wait_for(entered_cleanup.wait(), 2)
    overlap = asyncio.create_task(node.async_cleanup_cmd())
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    finalizers = [asyncio.create_task(node._finalize_lifecycle()) for _ in range(2)]
    await asyncio.sleep(0)
    assert not overlap.done()
    assert cleanup_proc.await_count == 0
    finish_cleanup.set()
    await asyncio.wait_for(asyncio.gather(overlap, *finalizers), 3)
    assert cleanups == [2, 1]
    assert cleanup_proc.await_count == 1
    assert node.cmd_procs[0].returncode == -signal.SIGHUP


async def test_topology_cleanup_before_coverage_and_destruction(
    topology, make_node, monkeypatch, tmp_path
):
    hook = tmp_path / "cleanup"
    node = make_node(
        "r1", [LifecycleStage(cmd="exec sleep 60", cleanup_cmd=f"touch {hook}")]
    )
    await topology.run()
    events = []

    async def coverage():
        assert hook.exists()
        assert node.cmd_p is not None
        events.append("coverage")

    async def destroy(unet):
        assert unet.process_exit_tasks is topology.process_exit_tasks
        assert events == ["coverage", "coverage-finish"]
        await node._finalize_lifecycle()
        events.append("destroy")

    topology.coverage_finish = AsyncMock(
        side_effect=lambda: events.append("coverage-finish")
    )
    topology.cfgopt = Mock()
    topology.cfgopt.getoption.side_effect = lambda option: option == "--coverage"
    monkeypatch.setattr(node, "gather_coverage_data", coverage, raising=False)
    # Bind the topology teardown to a Munet instance without privileged setup.
    unet = object.__new__(Munet)
    unet.__dict__.update(vars(topology))
    unet.deleting = True
    unet.built = False
    unet.name = "munet"
    monkeypatch.setattr(BaseMunet, "_async_delete", destroy)
    await unet._async_delete()
    assert events == ["coverage", "coverage-finish", "destroy"]


@pytest.mark.parametrize("already_exited", [True, False])
async def test_natural_exit_with_active_waiter(
    make_node, tmp_path, monkeypatch, already_exited
):
    release = tmp_path / "release"
    node = make_node(
        "r1",
        [LifecycleStage(cmd=f"while [ ! -f {release} ]; do sleep .01; done; exit 31")],
    )
    await node.run_cmd()
    [record] = node.cmd_procs
    if already_exited:
        release.touch()
        assert await asyncio.wait_for(record.task, 2) == 31
    else:
        original = node.async_cleanup_pid

        async def exit_during_cleanup(*args, **kwargs):
            release.touch()
            # Enter cleanup with a live proc, before it exits naturally.
            await asyncio.wait_for(asyncio.shield(record.task), 2)
            return await original(*args, **kwargs)

        monkeypatch.setattr(node, "async_cleanup_pid", exit_during_cleanup)

    await asyncio.wait_for(node._finalize_lifecycle(), 3)
    assert record.returncode == record.task.result() == 31
    assert node.cmd_procs == [record]


@pytest.mark.parametrize("cancel_watcher", [True, False])
async def test_kill_escalation_preserves_exit_status(
    make_node, tmp_path, monkeypatch, cancel_watcher
):
    started = tmp_path / "started"
    node = make_node(
        "r1",
        [
            LifecycleStage(
                cmd=f"trap '' HUP; touch {started}; exec sleep 60",
                ready_cmd=f"test -f {started}",
            )
        ],
    )
    await node.run_cmd()
    [record] = node.cmd_procs
    original = node.async_cleanup_pid

    async def quicker_cleanup(*args, **kwargs):
        return await original(*args, wait_sec=0.25, **kwargs)

    monkeypatch.setattr(node, "async_cleanup_pid", quicker_cleanup)
    if cancel_watcher:
        record.task.cancel()
        await asyncio.gather(record.task, return_exceptions=True)
    await asyncio.wait_for(node._finalize_lifecycle(), 3)
    assert record.returncode == record.proc.returncode == -signal.SIGKILL
    if not cancel_watcher:
        assert record.task.result() == -signal.SIGKILL


async def test_cleanup_timeout_does_not_discard_watcher(make_node, monkeypatch, caplog):
    node = make_node("r1", [LifecycleStage(cmd="exec sleep 60")])
    await node.run_cmd()
    [record] = node.cmd_procs
    timeout = AsyncMock(side_effect=TimeoutError("simulated signal timeout"))
    monkeypatch.setattr(node, "async_cleanup_pid", timeout)
    try:
        await asyncio.wait_for(node._finalize_lifecycle(), 1)
        await node._finalize_lifecycle()
        assert timeout.await_count == 1
        assert "SIGKILL timeout" in caplog.text
        assert not record.task.done()
    finally:
        record.proc.kill()
        await asyncio.wait_for(record.task, 2)
    assert record.returncode == -signal.SIGKILL


async def test_alternate_nsenter_child_pid(make_node):
    # A shell which forks and waits reproduces the nsenter process relationship.
    node = make_node("r1", [LifecycleStage(cmd="sleep 60 & wait")])
    node.nsenter_fork = True
    await asyncio.wait_for(node.run_cmd(), 5)
    [record] = node.cmd_procs
    assert record.pid is not None and record.pid != record.proc.pid
    assert node.pid_exists(record.pid)
    await asyncio.wait_for(node._finalize_lifecycle(), 3)
    assert not node.pid_exists(record.pid)
    assert record.task.done()
    assert record.returncode == record.task.result()
