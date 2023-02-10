# -*- coding: utf-8 eval: (blacken-mode 1) -*-
#
# September 13 2022, Christian Hopps <chopps@labn.net>
#
# Copyright 2022, LabN Consulting, L.L.C.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; see the file COPYING; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
#
"Tests of L3Qemu node type"
import logging
import os

import pytest

from common.fetch import fetch


# All tests are coroutines
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True, scope="module")
async def fetch_images():
    assets = ["bzImage", "rootfs.cpio.gz"]
    for asset in assets:
        if not os.path.exists(asset):
            break
    else:
        # XXX We neeed something better than existence.
        return
    fetch("LabNConsulting", "iptfs-dev", assets)


async def test_qemu_up(unet):
    r1 = unet.hosts["r1"]
    output = r1.monrepl.cmd_nostatus("info status")
    assert output == "VM status: running"


async def test_net_up(unet):
    h1 = unet.hosts["h1"]
    r1 = unet.hosts["r1"]

    h1mgmt0ip = h1.get_intf_addr("eth0").ip
    h1mgmt0ip6 = h1.get_intf_addr("eth0", ipv6=True).ip
    r1mgmt0ip = r1.get_intf_addr("eth0").ip
    r1mgmt0ip6 = r1.get_intf_addr("eth0", ipv6=True).ip

    h1r1ip = h1.get_intf_addr("eth1").ip
    h1r1ip6 = h1.get_intf_addr("eth1", ipv6=True).ip
    r1h1ip = r1.get_intf_addr("eth1").ip
    r1h1ip6 = r1.get_intf_addr("eth1", ipv6=True).ip

    logging.debug(h1.cmd_raises("ping -w1 -c1 192.168.0.254"))
    logging.debug(h1.cmd_raises(f"ping -w1 -c1 {r1mgmt0ip}"))
    logging.debug(h1.cmd_raises(f"ping -w1 -c1 {r1h1ip}"))
    # Need time for nbdisc
    h1.cmd_nostatus(f"ping -w5 -c3 {r1mgmt0ip6}", warn=False)
    logging.debug(h1.cmd_raises(f"ping -w1 -c1 {r1mgmt0ip6}"))
    logging.debug(h1.cmd_raises(f"ping -w1 -c1 {r1h1ip6}"))

    # Convert to SSH when we download the root-key artifact
    logging.debug(r1.conrepl.cmd_raises("ping -w1 -c1 192.168.0.254"))
    logging.debug(r1.conrepl.cmd_raises(f"ping -w1 -c1 {h1mgmt0ip}"))
    logging.debug(r1.conrepl.cmd_raises(f"ping -w1 -c1 {h1r1ip}"))

    # Need buildroot to support ipv6 ping
    logging.debug(r1.conrepl.cmd_raises("ping -w1 -c1 fd00::ff"))
    logging.debug(r1.conrepl.cmd_raises(f"ping -w1 -c1 {h1mgmt0ip6}"))
    logging.debug(r1.conrepl.cmd_raises(f"ping -w1 -c1 {h1r1ip6}"))
