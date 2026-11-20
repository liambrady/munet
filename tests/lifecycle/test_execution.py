# SPDX-License-Identifier: GPL-2.0-or-later
"""Stage execution, readiness, and topology ownership."""

import asyncio
import io
import sys

import pytest

from munet import cli
from munet.lifecycle import LifecycleStage


async def run_topology(topology):
    return await asyncio.wait_for(topology.run(), 5)


async def test_two_live_commands_and_independent_output(topology, make_node):
    kind_up = topology.rundir / "kind-up"
    node_up = topology.rundir / "node-up"
    node = make_node(
        "r1",
        [
            LifecycleStage(
                cmd=f"echo kind; echo kind-err >&2; touch {kind_up}; exec sleep 60",
                ready_cmd=f"test -f {kind_up}",
            ),
            LifecycleStage(
                cmd=f"echo node; echo node-err >&2; touch {node_up}; exec sleep 60",
                ready_cmd=f"test -f {node_up}",
            ),
        ],
    )
    tasks = await run_topology(topology)
    assert tasks == topology.process_exit_tasks == [p.task for p in node.cmd_procs]
    assert len(tasks) == 2
    assert not any(t.done() for t in tasks)
    assert all(state.entered and state.ready for state in node.stage_states)
    assert node.stage_states[0].process is node.cmd_procs[0]
    assert [record.stage_number for record in node.cmd_procs] == [1, 2]
    for i, body in enumerate(("kind", "node"), 1):
        assert (node.rundir / f"cmd.{i}.out").read_text() == f"{body}\n"
        assert (node.rundir / f"cmd.{i}.err").read_text() == f"{body}-err\n"


async def test_exit_status_is_diagnostic_and_readiness_keeps_polling(
    topology, make_node
):
    ready = topology.rundir / "ready"
    checks = topology.rundir / "checks"
    node = make_node(
        "r1",
        [
            LifecycleStage(cmd="exit 17", ready_cmd=f"touch {checks}; test -f {ready}"),
            LifecycleStage(cmd="exit 23"),
        ],
    )
    startup = asyncio.create_task(run_topology(topology))
    try:
        while not topology.process_exit_tasks or not checks.exists():
            if startup.done():
                await startup
            await asyncio.sleep(0.01)
        assert await asyncio.wait_for(node.cmd_procs[0].task, 2) == 17
        assert not node.stage_states[0].ready
        assert not node.stage_states[1].entered
        ready.touch()
        watchers = await startup
        assert await asyncio.gather(*watchers) == [17, 23]
        assert [p.returncode for p in node.cmd_procs] == [17, 23]
    finally:
        startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)


async def test_readiness_only_stage_and_inter_node_concurrency(topology, make_node):
    ready = topology.rundir / "other-node-stage"
    node = make_node(
        "r1",
        [
            LifecycleStage(ready_cmd=f"test -f {ready}"),
            LifecycleStage(cmd="echo r1"),
        ],
    )
    other = make_node("r2", [LifecycleStage(), LifecycleStage(cmd=f"touch {ready}")])
    tasks = await run_topology(topology)
    await asyncio.gather(*tasks)
    assert node.stage_states[0].ready and other.stage_states[0].ready
    for host in (node, other):
        assert not (host.rundir / "cmd.1.out").exists()
        assert (host.rundir / "cmd.2.out").is_file()


async def test_file_precedence_and_generic_cli_shell(topology, make_node, tmp_path):
    script = tmp_path / "startup.py"
    script.write_text(f"#!{sys.executable}\nprint('%NAME%')\n")
    node = make_node("r1", config={"cmd": "exit 99", "cmd-file": str(script)})
    await asyncio.gather(*(await run_topology(topology)))
    assert (node.rundir / "cmd.out").read_text() == "r1\n"
    assert (node.rundir / "cmd.err").read_text() == ""

    second = make_node(
        "r2",
        [
            LifecycleStage(cmd_file=str(script), cmd="exit 99"),
            LifecycleStage(cmd="print('python')", shell=sys.executable),
        ],
        config={"shell": sys.executable},
    )
    assert await (await second.run_cmd()).wait() == 0
    assert (second.rundir / "cmd.2.out").read_text() == "python\n"
    assert second.cmd_status("printf '%s' bash")[1] == "bash"
    assert (await second.async_cmd_status("printf '%s' async"))[1] == "async"
    output = io.StringIO()
    await cli.run_command(
        topology, output, "printf '%s' cli", "{}", False, ["r2"], False, []
    )
    assert output.getvalue() == "cli"


async def test_timeout_cancels_siblings_preserving_started_processes(
    topology, make_node
):
    gate = topology.rundir / "checking"
    cancelled = topology.rundir / "cancelled"
    node = make_node(
        "r1",
        [
            LifecycleStage(
                cmd="exec sleep 60", ready_cmd=f"touch {gate}; exec sleep 60"
            ),
            LifecycleStage(cmd="echo never"),
        ],
    )
    bad = make_node(
        "r2",
        [
            LifecycleStage(
                ready_cmd=f"test -f {gate} && false",
                ready_timeout=0.5,
                cleanup_cmd=f"touch {cancelled}",
            ),
            LifecycleStage(cmd="echo never"),
        ],
    )
    with pytest.raises(asyncio.TimeoutError, match=r"r2.*stage 1"):
        await run_topology(topology)
    assert gate.exists() and not cancelled.exists()
    assert not node.stage_states[1].entered and not bad.stage_states[1].entered
    [watcher] = topology.process_exit_tasks
    assert watcher is node.cmd_procs[0].task
    assert not watcher.done()
    await bad.async_cleanup_cmd()
    assert cancelled.exists()


async def test_failed_dispatch_enters_stage(topology, make_node):
    node = make_node(
        "r1",
        [
            LifecycleStage(cleanup_cmd="true"),
            LifecycleStage(cmd="/no-such-command", shell=False),
            LifecycleStage(cmd="echo never"),
        ],
    )
    with pytest.raises(FileNotFoundError):
        await run_topology(topology)
    assert [s.entered for s in node.stage_states] == [True, True, False]


async def test_node_without_commands_returns_no_watchers(topology, make_node):
    node = make_node("r1")
    assert await run_topology(topology) == []
    assert node.stage_states[0].ready


@pytest.mark.parametrize("shell", [True, False, "/bin/bash"])
async def test_explicit_shell_overrides_file_shebang(make_node, tmp_path, shell):
    script = tmp_path / "wrong-shebang"
    script.write_text("#!/no-such-interpreter\n/bin/echo override\n")
    node = make_node("r1", [LifecycleStage(cmd_file=str(script), shell=shell)])
    proc = await node.run_cmd()
    assert await proc.wait() == 0
    assert (node.rundir / "cmd.out").read_text() == "override\n"
