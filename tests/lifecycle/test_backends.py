# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise backend adapters with real local processes and stubbed runtimes."""

import asyncio
import sys

import pytest

from munet import native
from munet.base import Commander
from munet.lifecycle import LifecycleStage


@pytest.fixture
def fake_podman(topology, make_node, stub_namespace, monkeypatch):
    monkeypatch.setattr(native, "get_exec_path_host", lambda _: "/usr/bin/podman")

    def create(config, stages=None, boot=None):
        if stages is None:
            node = make_node("r1", config=config, cls=native.L3ContainerNode)
        else:
            node = native.L3ContainerNode(
                "r1",
                config,
                unet=topology,
                lifecycle_stages=stages,
                container_boot=boot,
            )
            topology.hosts[node.name] = node

        events = []
        mounted = {}

        async def ns_popen(args, **kwargs):
            events.append(args[1:])
            if args[1] == "run":
                for arg in args:
                    if arg.startswith("--volume=") and ":/tmp/" in arg:
                        host, dest = arg.removeprefix("--volume=").split(":", 1)
                        mounted[dest] = host
                return await Commander.async_popen_nsonly(
                    node, ["/bin/sh", "-c", "echo booted; exec sleep 60"], **kwargs
                )
            assert args[1:] == ["exec", node.container_id, "/bin/true"]
            return await Commander.async_popen_nsonly(node, ["/bin/true"], **kwargs)

        async def exec_popen(args, **kwargs):
            pre_cmd = node._get_pre_cmd(False, False)
            assert pre_cmd[:2] == ["/usr/bin/podman", "exec"]
            assert pre_cmd[-1] == node.container_id
            events.append(["exec", *args])
            return await Commander.async_popen_nsonly(
                node, [mounted.get(args[0], args[0]), *args[1:]], **kwargs
            )

        async def stop_or_remove(args, **_kwargs):
            assert all(p.proc.returncode is not None for p in node.cmd_procs)
            events.append(args[1:])
            if args[1] == "stop":
                node.container_p.terminate()
                await node.container_p.wait()
            return 0, "", ""

        monkeypatch.setattr(node, "async_popen_nsonly", ns_popen)
        monkeypatch.setattr(node, "async_popen", exec_popen)
        monkeypatch.setattr(node, "async_cmd_status_nsonly", stop_or_remove)
        return node, events, mounted

    return create


async def test_podman_exec_scripts_and_cleanup_before_stop(
    topology, fake_podman, tmp_path
):
    (tmp_path / "startup.py").write_text(f"#!{sys.executable}\nprint('%NAME%')\n")
    node, events, mounted = fake_podman(
        {"image": "test:latest"},
        [
            LifecycleStage(
                ready_cmd="true",
                cleanup_cmd="true",
            ),
            LifecycleStage(
                cmd_file="startup.py",
                cmd="exit 88",
                shell=sys.executable,
                cleanup_cmd="print('cleaned')",
            ),
        ],
        LifecycleStage(cmd="/sbin/init", shell=False),
    )
    try:
        tasks = await asyncio.wait_for(topology.run(), 3)
        assert topology.images == {"test:latest"}
        assert events[0][-2:] == ["test:latest", "/sbin/init"]
        assert events[1] == ["exec", node.container_id, "/bin/true"]
        assert events[2:] == [
            ["exec", "/tmp/ready-cmd.1.shebang"],
            ["exec", "/tmp/cmd.2.shebang"],
        ]
        assert set(mounted) == {
            "/tmp/ready-cmd.1.shebang",
            "/tmp/cleanup-cmd.1.shebang",
            "/tmp/cmd.2.shebang",
            "/tmp/cleanup-cmd.2.shebang",
        }
        assert len(tasks) == 2 and not tasks[0].done()
        assert node.container_p is node.cmd_p
        assert await tasks[1] == 0
        assert (node.rundir / "cmd.2.out").read_text() == "r1\n"
        assert (node.rundir / "container.out").read_text() == "booted\n"
    finally:
        await node._async_delete()
    assert events[-4:] == [
        ["exec", "/tmp/cleanup-cmd.2.shebang"],
        ["exec", "/tmp/cleanup-cmd.1.shebang"],
        ["stop", "--time=2", f"r1-{native.os.getpid()}"],
        ["rm", f"r1-{native.os.getpid()}"],
    ]
    assert tasks[0].done()


@pytest.mark.parametrize("explicit", [True, False])
async def test_podman_empty_boot_and_file_fallback(
    topology, fake_podman, tmp_path, explicit
):
    (tmp_path / "boot.sh").write_text("#!/bin/sh\necho custom\n")
    config = {"image": "test:latest", "cmd-file": "boot.sh"}
    if explicit:
        config["container-cmd"] = ""
    node, events, mounted = fake_podman(config)
    try:
        watchers = await asyncio.wait_for(topology.run(), 3)
        if explicit:
            assert events[0][-1] == "test:latest"
            assert events[2] == ["exec", "/tmp/cmd.shebang"]
            assert await watchers[1] == 0
            assert (node.rundir / "cmd.out").read_text() == "custom\n"
        else:
            assert events[0][-2:] == ["test:latest", "/tmp/container-cmd.shebang"]
            assert "/tmp/container-cmd.shebang" in mounted
            assert node.cmd_procs == []
            assert (node.rundir / "cmd.out").read_text() == "booted\n"
    finally:
        await node._async_delete()


async def test_ceos_boot_preserves_special_preparation(topology, fake_podman, tmp_path):
    (tmp_path / "startup.cfg").write_text("hostname r1\n")
    node, events, _ = fake_podman(
        {
            "image": "ceos",
            "kind": "ceos",
            "cmd": "/sbin/init",
            "shell": False,
            "env": {"INTFTYPE": "eth"},
            "startup-config": "startup.cfg",
            "system-mac": "00:1c:73:11:22:33",
        }
    )
    try:
        await asyncio.wait_for(topology.run(), 3)
        assert f"--volume={node.rundir / 'flash'}:/mnt/flash" in events[0]
        assert events[0][-3:] == ["ceos", "/sbin/init", "systemd.setenv=INTFTYPE=eth"]
        assert (node.rundir / "flash/startup-config").read_text() == "hostname r1\n"
        assert (
            node.rundir / "flash/system_mac_address"
        ).read_text() == "00:1c:73:11:22:33\n"
    finally:
        await node._async_delete()


@pytest.mark.parametrize(
    "use_stdio,with_ssh,disk_created",
    [
        (True, False, True),
        (False, True, False),
    ],
)
async def test_qemu_initialization_stage_console_and_cleanup(
    topology,
    make_node,
    stub_namespace,
    monkeypatch,
    tmp_path,
    use_stdio,
    with_ssh,
    disk_created,
):
    initial = tmp_path / "initial.sh"
    initial.write_text("#!/bin/sh\necho first-boot\n")
    node = make_node(
        "vm",
        [
            LifecycleStage(
                cmd="echo kind", ready_cmd="test -f ready", cleanup_cmd="stop-kind"
            ),
            LifecycleStage(shell=False, cleanup_cmd="stop-node"),
        ],
        config={
            "connections": [],
            "cmd": "",
            "qemu": {
                "console": {
                    "stdio": use_stdio,
                    "sends": ["login"],
                    "expects": ["prompt"],
                },
                "initial-cmd": "ignored",
                "initial-cmd-file": str(initial),
            },
        },
        cls=native.L3QemuVM,
    )
    events = []
    node.disk_created = disk_created
    node.ssh_keyfile = "key" if with_ssh else None
    monkeypatch.setattr(node, "_L3QemuVM__setup_ssh", lambda: True)

    class Repl:
        def __init__(self, label):
            self.label = label

        def cmd_status(self, command, **_kwargs):
            assert node.launch_p.returncode is None
            events.append((self.label, command))
            if command == "/tmp/cmd.1.shebang":
                return 9, "startup exited"
            return 0, ""

        def cmd_nostatus(self, command):
            return ""

    async def runtime(args, **kwargs):
        assert any("/_cmdcon" in arg for arg in args) == use_stdio
        return await Commander.async_popen_nsonly(node, ["/bin/sleep", "60"], **kwargs)

    async def consoles(*labels, **kwargs):
        assert labels == (("_console", "_cmdcon") if use_stdio else ("_console",))
        assert kwargs["sends"] == ["login"] and kwargs["expects"] == ["prompt"]
        return [Repl(label) for label in labels]

    async def monitor(_socket):
        return Repl("monitor")

    async def renumber():
        pass

    monkeypatch.setattr(node, "async_popen_nsonly", runtime)
    monkeypatch.setattr(node, "_opencons", consoles)
    monkeypatch.setattr(node, "monitor", monitor)
    monkeypatch.setattr(node, "renumber_interfaces", renumber)
    monkeypatch.setattr(native, "get_exec_path_host", lambda _: "/bin/sleep")
    active = "_cmdcon" if use_stdio else "_console"
    try:
        [watcher] = await asyncio.wait_for(topology.run(), 3)
        assert node.use_ssh == with_ssh
        assert not watcher.done()
        assert node.cmd_procs == [] and len(node.stage_states) == 2
        invoked = [
            event
            for event in events
            if event[0] == active and not event[1].startswith(("cat ", "chmod "))
        ]
        expected = [
            (active, "/tmp/cmd.1.shebang"),
            (active, "/tmp/ready-cmd.1.shebang"),
        ]
        if disk_created:
            expected.insert(0, (active, "/tmp/initial-cmd-file.shebang"))
        assert invoked == expected
    finally:
        await node._async_delete()
    assert events[-1] == (active, "/tmp/cleanup-cmd.1.shebang")
    assert (active, "stop-node") in events
    assert watcher.done()


def test_qemu_readiness_only_does_not_allocate_command_console(
    make_node, stub_namespace
):
    node = make_node(
        "vm",
        [LifecycleStage(ready_cmd="true", cleanup_cmd="true")],
        config={"qemu": {"console": {"stdio": True}}},
        cls=native.L3QemuVM,
    )
    assert not node._use_command_console(True)
