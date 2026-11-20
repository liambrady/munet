# SPDX-License-Identifier: GPL-2.0-or-later
"""Lifecycle configuration and precedence."""

import pytest

from munet.config import config_subst
from munet.config import merge_kind_config
from munet.lifecycle import LifecycleStage
from munet.lifecycle import resolve_lifecycle
from munet.native import Munet
from munet.parser import load_kinds


def resolve(kind, node, name="r1"):
    context = dict(name=name, rundir=f"/run/{name}", configdir="/topology")
    merged = config_subst(merge_kind_config(kind, node), **context)
    return resolve_lifecycle(node, kind, merged, **context)


def test_default_stage():
    assert resolve({}, {}) == ([LifecycleStage()], None)
    kind = {"cmd": "kind", "shell": False, "ready-timeout": 10}
    node = {"cmd": "node"}
    assert resolve(kind, node) == (
        [LifecycleStage(cmd="node", shell=False, ready_timeout=10)],
        None,
    )


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
    assert resolve(kind, node, name)[0] == [
        LifecycleStage(
            cmd=f"kind {name}",
            shell="/bin/dash",
            ready_timeout=7,
            cleanup_cmd=f"rm /run/{name}/kind",
        ),
        LifecycleStage(
            cmd_file="/topology/run", ready_cmd=f"test -f /run/{name}/ready"
        ),
    ]
    assert kind["cmd"] == "kind %NAME%"


@pytest.mark.parametrize("boot", ["", "daemon %NAME%"])
def test_explicit_boot(boot, caplog):
    kind = {"merge": ["commands"], "cmd": "kind", "container-cmd": "kind-daemon"}
    stages, stage = resolve(
        kind, {"image": "test", "cmd": "node", "container-cmd": boot}
    )
    assert [s.cmd for s in stages] == ["kind", "node"]
    assert stage.cmd == boot.replace("%NAME%", "r1")
    assert "legacy" not in caplog.text


@pytest.mark.parametrize(
    "merge,kind_cmd,selected,remaining",
    [
        ([], "kind", "node", [None]),
        (["commands"], "kind", "kind", [None, "node"]),
        (["commands"], "", "node", ["", None]),
    ],
)
def test_legacy_boot(merge, kind_cmd, selected, remaining, caplog):
    kind = {"merge": merge, "cmd": kind_cmd, "cleanup-cmd": "cleanup"}
    stages, boot = resolve(kind, {"image": "test", "cmd": "node", "ready-cmd": "check"})
    assert boot.cmd == selected
    assert [stage.cmd for stage in stages] == remaining
    assert stages[0].cleanup_cmd == "cleanup"
    assert stages[-1].ready_cmd == "check"
    assert "container-cmd" in caplog.text


def test_boot_prefers_file_and_consumes_startup(caplog):
    stages, boot = resolve({}, {"image": "test", "cmd": "ignored", "cmd-file": "boot"})
    assert boot.cmd_file == "boot"
    assert not stages[0].has_startup
    assert resolve({}, {"image": "test"}) == ([LifecycleStage()], None)
    assert "legacy cmd-file" in caplog.text


async def test_build_uses_effective_kind(tmp_path):
    for directory, contents in (
        ("first", "{name: router, cmd: shadowed}"),
        (
            "second",
            "{name: router, merge: [commands, cap-add], cmd: 'kind %NAME%', cap-add: [A]}",
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
