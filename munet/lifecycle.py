# SPDX-License-Identifier: GPL-2.0-or-later
"""Configuration and state for node lifecycle commands."""

from __future__ import annotations

import asyncio
import logging

from dataclasses import dataclass
from dataclasses import replace

from .config import config_subst


@dataclass(frozen=True)
class LifecycleStage:
    """Lifecycle settings from one configuration source."""

    cmd: str | None = None
    cmd_file: str | None = None
    ready_cmd: str | None = None
    ready_timeout: int | None = None
    shell: str | bool | None = None
    cleanup_cmd: str | None = None

    @classmethod
    def from_config(cls, config, **context):
        fields = {
            key: config[key.replace("_", "-")]
            for key in cls.__dataclass_fields__
            if key.replace("_", "-") in config
        }
        return cls(**(config_subst(fields, **context) if context else fields))

    @property
    def has_startup(self):
        return bool((self.cmd_file or "").strip() or (self.cmd or "").strip())


@dataclass
class CmdProcess:
    """Local startup subprocess and its completion information."""

    proc: asyncio.subprocess.Process
    stage_number: int
    pid: int | None = None
    task: asyncio.Task | None = None
    returncode: int | None = None


@dataclass
class StageState:
    """Progress and subprocess association for one stage."""

    stage: LifecycleStage
    number: int
    entered: bool = False
    ready: bool = False
    process: CmdProcess | None = None


def resolve_lifecycle(node, kind, merged, **context):
    """Extract ordered stages and resolve Podman boot from the merged config.

    The merged configuration already has the node substitutions applied.
    """
    if "commands" in kind.get("merge", []):
        stages = [
            LifecycleStage.from_config(source, **context) for source in (kind, node)
        ]
    else:
        stages = [LifecycleStage.from_config(merged)]

    boot = None
    if merged.get("image"):
        if "container-cmd" in merged:
            boot = LifecycleStage(
                cmd=merged["container-cmd"], shell=merged.get("shell")
            )
        else:
            for i, stage in enumerate(stages):
                if stage.has_startup:
                    boot = LifecycleStage(
                        cmd=stage.cmd, cmd_file=stage.cmd_file, shell=stage.shell
                    )
                    stages[i] = replace(stage, cmd=None, cmd_file=None)
                    logging.warning(
                        "%s: using legacy %s to boot the container; configure "
                        "container-cmd to select the boot command explicitly",
                        context.get("name", merged.get("name")),
                        "cmd-file" if (stage.cmd_file or "").strip() else "cmd",
                    )
                    break
    return stages, boot
