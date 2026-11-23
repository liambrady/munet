# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise lifecycle composition in a real Linux namespace."""

import asyncio

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("unet_perfunc_share", ["munet-composed"], indirect=True)
async def test_config_composed_commands(unet_perfunc_share):
    node = unet_perfunc_share.hosts["h-composed"]
    first, second = node.cmd_procs
    assert await asyncio.wait_for(asyncio.shield(second.task), 5) == 0
    assert first.proc.returncode is None
    assert node.cmd_raises("cat cmd.1.out cmd.2.out") == "kind\nnode\n"
    await node.async_cleanup_cmd()
    assert node.cmd_raises("cat cleanup-order") == "node\nkind\n"
    assert first.proc.returncode is None
