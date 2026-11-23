# -*- coding: utf-8 eval: (blacken-mode 1) -*-
# SPDX-License-Identifier: GPL-2.0-or-later
#
# May 20 2025, Liam Brady <lbrady@labn.net>
#
# Copyright 2021, LabN Consulting, L.L.C.
#
"Testing of cmd and cmd-file config for L3NamespaceNode"

import asyncio
import logging

import pytest

# Await process watchers on the same loop as the module-scoped topology.
pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest.mark.parametrize(
    "host",
    ["h-shcmd", "h-shcmd-file", "h-pycmd", "h-pycmd-file", "h-shcmd-file-noshebang"],
)
async def test_config_cmd(unet_share, host):
    unet = unet_share
    rn = unet.hosts[host]
    await asyncio.wait_for(asyncio.shield(rn.cmd_procs[0].task), 5)
    output = rn.cmd_raises("cat cmd.out")
    logging.debug("expects to find 'foo' in cmd.out; Found: %s", output)
    assert "foo" in output
