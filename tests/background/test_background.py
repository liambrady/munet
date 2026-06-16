# -*- coding: utf-8 eval: (blacken-mode 1) -*-
# SPDX-License-Identifier: GPL-2.0-or-later
#
# September 03 2026, Liam Brady <lbrady@labn.net>
#
# Copyright 2026, LabN Consulting, L.L.C.
#
"Testing of basic background command API."


import logging
import re
import time
import pytest

from munet import Munet

# All tests are coroutines
pytestmark = pytest.mark.asyncio


async def test_background_capture(unet):
    r1 = unet.hosts["r1"]
    r2 = unet.hosts["r2"]

    pid = r2.background_cmd_start(f"tcpdump -f icmp")
    time.sleep(1)  # Give time for the capture to start

    oifname = 'eth0'
    oip = r2.get_intf_addr(oifname).ip
    rv, out, _ = await r1.async_cmd_status(f"ping -c3 {oip}")
    assert rv == 0
    assert "bytes from 10.0.1.2" in out
    assert "3 packets transmitted" in out

    rv, out, err = r2.background_cmd_end_status(pid, timeout=5)
    assert rv != 0  # This process was forecfully terminated!
    assert "10.0.1.2: ICMP echo request" in out
    assert "6 packets received by filter" in err


async def test_background_early_finish(unet):
    r1 = unet.hosts["r1"]
    r2 = unet.hosts["r2"]

    pid = r2.background_cmd_start(f"echo foo")

    time.sleep(1)

    rv, out, _ = r2.background_cmd_end_status(pid, timeout=5)
    assert rv == 0  # This process exited normally!
    assert "foo" in out


async def test_background_forgotten(unet):
    r1 = unet.hosts["r1"]
    r2 = unet.hosts["r2"]

    pid = r2.background_cmd_start(f"tcpdump -f icmp")
    # Oops! We forgot about this process. Let the topology clean it up.
    # There should be no errors in the test logs.
