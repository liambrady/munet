# SPDX-License-Identifier: GPL-2.0-or-later
"""Unprivileged fixtures using real node lifecycle and Commander subprocess code."""

import logging

import pytest

from munet.base import Commander
from munet.config import ConfigOptionsProxy
from munet.native import L3NodeMixin
from munet.native import LinuxNamespace
from munet.native import Munet
from munet.native import NodeMixin


class LocalNode(NodeMixin, Commander):
    """A node with no namespace overhead."""

    mgmt_ip = None
    mgmt_ip6 = None
    nsenter_fork = False


class LocalTopology:
    run = Munet.run

    def __init__(self, tmp_path):
        self.rundir = tmp_path
        self.config_dirname = str(tmp_path)
        self.cfgopt = ConfigOptionsProxy()
        self.logger = logging.getLogger(__name__)
        self.hosts = {}
        self.process_exit_tasks = []
        self.rootcmd = Commander("lifecycle-root")
        self.proc_path = "/proc"

    async def load_images(self, images):
        self.images = images


@pytest.fixture
async def topology(tmp_path):
    unet = LocalTopology(tmp_path)
    yield unet
    for node in unet.hosts.values():
        await node._finalize_lifecycle()


@pytest.fixture
def make_node(topology):
    def make(name, stages=None, config=None, cls=LocalNode):
        options = {} if stages is None else {"lifecycle_stages": stages}
        node = cls(name, config=config or {}, unet=topology, **options)
        topology.hosts[name] = node
        return node

    return make


@pytest.fixture
def stub_namespace(monkeypatch):
    def namespace_init(self, name, *, unet, **_kwargs):
        Commander.__init__(self, name, unet=unet)
        self.host_intfs = {}
        self.phy_intfs = {}
        self.pid = 1
        self.nsenter_fork = False

    # Replace privileged provisioning only. Dispatch still uses the real adapters.
    monkeypatch.setattr(L3NodeMixin, "__init__", NodeMixin.__init__)
    monkeypatch.setattr(LinuxNamespace, "__init__", namespace_init)
    monkeypatch.setattr(LinuxNamespace, "_get_pre_cmd", Commander._get_pre_cmd)
    monkeypatch.setattr(LinuxNamespace, "_async_delete", Commander._async_delete)
