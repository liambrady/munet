# SPDX-License-Identifier: GPL-2.0-or-later
"""Lifecycle configuration and precedence."""

from unittest.mock import Mock

import pytest

from munet.config import config_subst
from munet.config import merge_kind_config
from munet.lifecycle import resolve_lifecycle
from munet.native import L3ContainerNode
from munet.native import L3QemuVM
from munet.native import Munet
from munet.parser import load_kinds


def resolve(kind, node, name="r1"):
    context = dict(name=name, rundir=f"/run/{name}", configdir="/topology")
    merged = config_subst(merge_kind_config(kind, node), **context)
    return resolve_lifecycle(node, kind, merged, **context)


def test_default_stage():
    [empty] = resolve({}, {})
    assert not empty.has_startup
    kind = {"cmd": "kind", "shell": False, "ready-timeout": 10}
    [stage] = resolve(kind, {"cmd": "node"})
    assert stage.cmd == "node"
    assert stage.shell is False
    assert stage.ready_timeout == 10


@pytest.mark.parametrize("name", ["r1", "r2"])
def test_composed_stage_substitution(name):
    kind = {
        "merge": ["commands"],
        "cmd": "kind %NAME%",
        "shell": "/bin/dash",
        "ready-timeout": 7,
        "cleanup-cmd": "rm %RUNDIR%/kind",
    }
    node = {"cmd-file": "%CONFIGDIR%/run", "ready-cmd": "test -f %RUNDIR%/ready"}
    first, second = resolve(kind, node, name)
    assert first.cmd == f"kind {name}"
    assert first.shell == "/bin/dash"
    assert first.ready_timeout == 7
    assert first.cleanup_cmd == f"rm /run/{name}/kind"
    assert second.cmd_file == "/topology/run"
    assert second.ready_cmd == f"test -f /run/{name}/ready"
    assert second.shell is None
    assert kind["cmd"] == "kind %NAME%"


async def build_node(topology, make_node, kind, node, cls):
    topology.built = topology.autonumber = topology.ipv6_enable = False
    topology.config = {"kinds": {"test-kind": kind}}
    topology.topoconf = {"nodes": [{"name": "r1", "kind": "test-kind", **node}]}

    def add_node(name, config, lifecycle_stages, logger=None, **options):
        del logger
        return make_node(name, lifecycle_stages, config, cls, **options)

    topology.add_l3_node = add_node
    await Munet._async_build(topology)
    return topology.hosts["r1"]


@pytest.mark.parametrize("merge", [[], ["commands"]])
@pytest.mark.parametrize("override", [None, "", "node-boot %NAME%"])
async def test_explicit_boot(topology, make_node, stub_namespace, merge, override):
    kind = {
        "merge": merge,
        "cmd": "kind",
        "container-cmd": "kind-boot %NAME%",
        "shell": "/bin/dash",
        "image": "test",
    }
    config = {"cmd": "follow-up", "shell": False}
    if override is not None:
        config["container-cmd"] = override
    node = await build_node(topology, make_node, kind, config, L3ContainerNode)
    expected_cmd = kind["container-cmd"] if override is None else override
    assert node.container_boot.cmd == expected_cmd.replace("%NAME%", "r1")
    assert node.container_boot.shell == (
        "/bin/dash" if merge and override is None else False
    )
    assert node.has_run_cmd()


@pytest.mark.parametrize(
    "merge,kind_cmd,selected,has_startup",
    [
        ([], "kind", "node", False),
        (["commands"], "kind", "kind", True),
        (["commands"], "", "node", False),
    ],
)
async def test_legacy_boot(
    topology, make_node, stub_namespace, caplog, merge, kind_cmd, selected, has_startup
):
    kind = {"merge": merge, "cmd": kind_cmd, "cleanup-cmd": "cleanup"}
    node = await build_node(
        topology,
        make_node,
        kind,
        {"image": "test", "cmd": "node", "ready-cmd": "check"},
        L3ContainerNode,
    )
    assert node.container_boot.cmd == selected
    assert node.has_run_cmd() is has_startup
    assert node.has_cleanup_cmd() and node.has_ready_cmd()
    assert "container-cmd" in caplog.text


@pytest.mark.parametrize(
    "merge,kind_init,node_init,expected_shell,expected_name",
    [
        ([], {"initial-cmd": "kind"}, {}, False, "initial-cmd"),
        (["commands"], {"initial-cmd": "kind"}, {}, "/bin/dash", "initial-cmd"),
        (
            ["commands", "qemu"],
            {"initial-cmd-file": "kind.sh"},
            {"initial-cmd": "node"},
            "/bin/dash",
            "initial-cmd-file",
        ),
        (
            ["commands"],
            {"initial-cmd-file": "kind.sh"},
            {"initial-cmd": "node"},
            False,
            "initial-cmd",
        ),
        (
            ["commands", "qemu"],
            {"initial-cmd": "kind"},
            {"initial-cmd-file": "node.sh"},
            False,
            "initial-cmd-file",
        ),
    ],
)
async def test_qemu_initializer_uses_command_source_shell(
    topology,
    make_node,
    stub_namespace,
    monkeypatch,
    merge,
    kind_init,
    node_init,
    expected_shell,
    expected_name,
):
    kind = {"merge": merge, "qemu": kind_init, "shell": "/bin/dash"}
    config = {"cmd": "follow-up", "shell": False}
    if node_init:
        config["qemu"] = node_init
    node = await build_node(topology, make_node, kind, config, L3QemuVM)
    dispatch = Mock()
    monkeypatch.setattr(node, "_run_cmd", dispatch)
    node._run_initial_guest_command()
    dispatch.assert_not_called()
    node.disk_created = True
    node._run_initial_guest_command()
    dispatch.assert_called_once()
    initializer, _, name = dispatch.call_args.args
    assert name == expected_name
    assert initializer.shell == expected_shell


async def test_build_uses_effective_kind(tmp_path):
    for directory, contents in (
        ("first", "{name: router, cmd: shadowed}"),
        (
            "second",
            "{name: router, merge: [commands, cap-add], "
            "cmd: 'kind %NAME%', cap-add: [A]}",
        ),
    ):
        source = tmp_path / directory
        source.mkdir()
        (source / "kinds.yaml").write_text(f"kinds: [{contents}]\n")

    class Topology:
        built = False
        autonumber = False
        ipv6_enable = False
        rundir = tmp_path / "run"
        config_dirname = str(tmp_path)
        config = load_kinds(None, [str(tmp_path / src) for src in ("first", "second")])
        topoconf = {
            "nodes": [{"name": "r1", "kind": "router", "cmd": "node", "cap-add": ["B"]}]
        }
        hosts = {}

        def add_l3_node(self, name, config, *, lifecycle_stages, **_kwargs):
            assert name == "r1"
            assert config["cmd"] == "node"
            assert config["cap-add"] == ["A", "B"]
            assert [stage.cmd for stage in lifecycle_stages] == ["kind r1", "node"]

    await Munet._async_build(Topology())
