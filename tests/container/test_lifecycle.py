# SPDX-License-Identifier: GPL-2.0-or-later
"""Lifecycle commands exercised with the CI container image."""

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("unet_perfunc_share", ["munet-lifecycle"], indirect=True)
async def test_container_lifecycles(unet_perfunc_share):
    unet = unet_perfunc_share
    composed = unet.hosts["composed"]
    assert (composed.rundir / "container.out").read_text() == "booted\n"
    assert (composed.rundir / "cmd.1.out").read_text() == "kind\n"
    assert (composed.rundir / "cmd.2.out").read_text() == "node\n"
    composed.cmd_raises('kill -0 "$(cat /kind.pid)"')
    runtime = composed.cmd_p

    legacy = unet.hosts["legacy"]
    assert (legacy.rundir / "container.out").read_text() == "legacy-boot\n"
    assert legacy.cmd_raises("cat /legacy-count") == "boot\n"
    assert (legacy.rundir / "cmd.2.out").read_text() == "follow-up\n"

    image_default = unet.hosts["empty-boot"]
    assert (image_default.rundir / "cmd.out").read_text() == "empty-boot\n"
    assert (
        unet.hosts["single"].rundir / "container.out"
    ).read_text() == "merged-shell\n"
    assert (
        unet.hosts["node-boot"].rundir / "container.out"
    ).read_text() == "node-boot\n"

    ceos = unet.hosts["ceos-test"]
    assert (ceos.rundir / "container.out").read_text().splitlines() == [
        "/sbin/init",
        "systemd.setenv=INTFTYPE=eth",
    ]
    assert ceos.cmd_raises("cat /mnt/flash/startup-config") == "hostname ceos-test\n"
    assert ceos.cmd_raises("cat /mnt/flash/system_mac_address") == "00:1c:73:11:22:33\n"

    await unet.async_delete()
    assert (composed.rundir / "results/cleanup-order").read_text() == "node\nkind\n"
    assert runtime.returncode is not None
