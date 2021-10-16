# -*- coding: utf-8 eval: (blacken-mode 1) -*-
#
# October 1 2021, Christian Hopps <chopps@labn.net>
#
# Copyright (c) 1, LabN Consulting, L.L.C.
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
import asyncio
import ipaddress
import logging
import os
import re
import shlex
import subprocess
import tempfile

from .base import BaseMunet
from .base import Bridge
from .base import LinuxNamespace
from .base import Timeout
from .base import acomm_error
from .base import cmd_error
from .base import get_exec_path_host


def get_loopback_ips(c, nid):
    if "ip" in c and c["ip"]:
        if c["ip"] == "auto":
            return [ipaddress.ip_interface("10.255.0.0/32") + nid]
        if isinstance(c["ip"], str):
            return [ipaddress.ip_interface(c["ip"])]
        return [ipaddress.ip_interface(x) for x in c["ip"]]
    return []


# Uneeded
# def get_ip_config(cconfig, cname, remote_name=None):
#     "Get custom IP config for a given connection"
#     for c in cconfig:
#         if isinstance(x, str):
#             if c == cname:
#                 return None
#             continue
#         if "to" not in c or c["to"] != cname:
#             continue
#         if remote_name and ("remote_name" not in c or
#             c["remote_name"] != remote_name):
#             continue
#         return c["ip"] if "ip" in c else None
#     return None


def get_ip_network(c):
    if "ip" in c and c["ip"] != "auto":
        return ipaddress.ip_network(c["ip"])
    return None


def make_ip_network(net, inc):
    n = ipaddress.ip_network(net)
    return ipaddress.ip_network(
        (n.network_address + inc * n.num_addresses, n.prefixlen)
    )


async def to_thread(func):
    """to_thread for python < 3.9"""
    try:
        return await asyncio.to_thread(func)
    except AttributeError:
        logging.warning("Using backport to_thread")
        return await asyncio.get_running_loop().run_in_executor(None, func)


class L3Bridge(Bridge):
    """
    A linux bridge.
    """

    def __init__(self, name=None, unet=None, logger=None, config=None):
        """Create a linux Bridge."""

        super().__init__(name=name, unet=unet, logger=logger)

        self.config = config if config else {}
        self.unet = unet

        ip = get_ip_network(self.config)
        self.ip = ip if ip else make_ip_network("10.0.0.0/24", self.id)
        self.cmd_raises(f"ip addr add {self.ip} dev {name}")
        self.logger.debug("%s: set network address to %s", self, self.ip)


class L3Node(LinuxNamespace):
    next_ord = 1

    @classmethod
    def _get_next_ord(cls):
        n = cls.next_ord
        cls.next_ord = n + 1
        return n

    def __init__(self, name=None, unet=None, config=None, **kwargs):
        """Create a linux Bridge."""

        self.config = config if config else {}
        config = self.config

        self.cmd_p = None
        self.container_id = ""
        self.id = int(config["id"]) if "id" in config else self._get_next_ord()
        self.unet = unet

        if not name:
            name = "r{}".format(self.id)
        super().__init__(name=name, **kwargs)

        self.mount_volumes()

        # -----------------------
        # Setup node's networking
        # -----------------------

        self.next_p2p_network = ipaddress.ip_network(f"10.254.{self.id}.0/31")

        self.loopback_ip = None
        self.loopback_ips = get_loopback_ips(self.config, self.id)
        self.loopback_ip = self.loopback_ips[0] if self.loopback_ips else None
        if self.loopback_ip:
            self.cmd_raises_host(f"ip addr add {self.loopback_ip} dev lo")
            self.cmd_raises_host("ip link set lo up")
            for i, ip in enumerate(self.loopback_ips[1:]):
                self.cmd_raises_host(f"ip addr add {ip} dev lo:{i}")

        # -------------------
        # Setup node's rundir
        # -------------------

        self.rundir = os.path.join(unet.rundir, name)
        self.cmd_raises_host(f"mkdir -p {self.rundir}")
        # Not host path based, but we assume same
        self.set_cwd(self.rundir)

        hosts_file = os.path.join(self.rundir, "hosts.txt")
        if os.path.exists(hosts_file):
            self.bind_mount(os.path.join(self.rundir, "hosts.txt"), "/etc/hosts")

    def mount_volumes(self):
        if "volumes" not in self.config:
            return

        for m in self.config["volumes"]:
            if isinstance(m, str):
                s = m.split(":", 1)
                if len(s) == 1:
                    self.tmpfs_mount(s[0])
                else:
                    spath = s[0]
                    if spath[0] == ".":
                        spath = os.path.abspath(
                            os.path.join(
                                os.path.dirname(self.config["config_pathname"]), spath
                            )
                        )
                    self.bind_mount(spath, s[1])
                continue
            raise NotImplementedError("complex mounts for non-containers")

    def get_ifname(self, netname):
        for c in self.config["connections"]:
            if c["to"] == netname:
                return c["name"]
        return None

    async def run_cmd(self):
        """Run the configured commands for this node"""

        self.logger.debug(
            "[rundir %s exists %s]", self.rundir, os.path.exists(self.rundir)
        )

        cmd = self.config.get("cmd", "").strip()
        if not cmd:
            return None
        cmd += "\n"
        cmdpath = os.path.join(self.rundir, "cmd.txt")
        self.logger.debug("[cmdpath %s]", cmdpath)
        with open(cmdpath, mode="w+", encoding="utf-8") as cmdfile:
            cmdfile.write(cmd)
            cmdfile.flush()

        # This command just takes too long
        # bash_path = await self.async_get_exec_path("bash")
        bash_path = "/bin/bash"
        cmds = [bash_path, cmdpath]
        self.cmd_p = await self.async_popen(
            cmds,
            stdin=subprocess.DEVNULL,
            stdout=open(os.path.join(self.rundir, "cmd.out"), "wb"),
            stderr=open(os.path.join(self.rundir, "cmd.err"), "wb"),
            # start_new_session=True,  # allows us to signal all children to exit
        )
        self.logger.debug(
            "%s: async_popen %s => %s",
            self,
            cmds,
            self.cmd_p.pid,
        )
        return self.cmd_p

    def cmd_completed(self, future):
        try:
            n = future.result()
            self.logger.info("%s: cmd completed result: %s", self, n)
        except asyncio.CancelledError:
            # Should we stop the container if we have one?
            self.logger.debug("%s: cmd.wait() canceled", future)

    # def child_exit(self, pid):
    #     """Called back when cmd finishes executing."""
    #     if self.cmd_p && self.cmd_p.pid == pid:
    #         self.container_id = None

    def set_lan_addr(self, cconf, switch):
        self.logger.debug(
            "%s: prefixlen of switch %s is %s", self, switch.name, switch.ip.prefixlen
        )
        if "ip" in cconf:
            ipaddr = ipaddress.ip_interface(cconf["ip"]) if "ip" in cconf else None
        else:
            n = switch.ip
            ipaddr = ipaddress.ip_interface((n.network_address + self.id, n.prefixlen))
        ifname = cconf["name"]
        self.intf_addrs[ifname] = ipaddr
        self.logger.debug("%s: adding %s to lan intf %s", self, ipaddr, ifname)
        self.intf_ip_cmd(ifname, f"ip addr add {ipaddr} dev {ifname}")

    def set_p2p_addr(self, cconf, other, occonf):
        if "ip" in cconf:
            ipaddr = ipaddress.ip_interface(cconf["ip"]) if cconf["ip"] else None
            oipaddr = ipaddress.ip_interface(occonf["ip"]) if occonf["ip"] else None
        else:
            n = self.next_p2p_network
            self.next_p2p_network = make_ip_network(n, 1)

            ipaddr = ipaddress.ip_interface(n)
            oipaddr = ipaddress.ip_interface((ipaddr.ip + 1, n.prefixlen))

        ifname = cconf["name"]
        oifname = occonf["name"]

        self.intf_addrs[ifname] = ipaddr
        other.intf_addrs[oifname] = oipaddr

        if ipaddr:
            self.logger.debug("%s: adding %s to p2p intf %s", self, ipaddr, ifname)
            self.intf_ip_cmd(ifname, f"ip addr add {ipaddr} dev {ifname}")

        if oipaddr:
            self.logger.debug(
                "%s: adding %s to other p2p intf %s", other, oipaddr, oifname
            )
            other.intf_ip_cmd(oifname, f"ip addr add {oipaddr} dev {oifname}")

    async def async_delete(self):
        if type(self) == L3Node:  # pylint: disable=C0123
            self.logger.info("%s: node async deleting", self.name)
        else:
            self.logger.debug("%s: node async_delete", self.name)
        await self.async_cleanup_proc(self.cmd_p)
        await super().async_delete()

    def delete(self):
        asyncio.run(L3Node.async_delete(self))


class L3ContainerNode(L3Node):
    "A node that runs a container image using podman"

    def __init__(self, name=None, config=None, **kwargs):
        """Create a linux Bridge."""

        if not config:
            config = {}

        self.cont_exec_paths = {}
        self.container_id = None
        self.container_image = config.get("image", "")
        self.extra_mounts = []
        assert self.container_image

        super().__init__(
            name=name,
            config=config,
            # cgroup=True,
            # pid=True,
            # private_mounts=["/sys/fs/cgroup:/sys/fs/cgroup"],
            **kwargs,
        )

    @property
    def is_container(self):
        return True

    def get_exec_path(self, binary):
        """Return the full path to the binary executable inside the image.

        `binary` :: binary name or list of binary names
        """
        return self._get_exec_path(binary, self.cmd_status, self.cont_exec_paths)

    async def async_get_exec_path(self, binary):
        """Return the full path to the binary executable inside the image.

        `binary` :: binary name or list of binary names
        """
        path = await self._async_get_exec_path(
            binary, self.async_cmd_status, self.cont_exec_paths
        )
        return path

    def get_exec_path_host(self, binary):
        """Return the full path to the binary executable on the host.

        `binary` :: binary name or list of binary names
        """
        return get_exec_path_host(binary)

    def _get_podman_precmd(self, cmd, sudo=False, tty=False):
        cmds = []
        if sudo:
            cmds.append(get_exec_path_host("sudo"))
        cmds.append(get_exec_path_host("podman"))
        if self.container_id:
            cmds.append("exec")
            if tty:
                cmds.append("-it")
            cmds.append(self.container_id)
        else:
            cmds += [
                "run",
                "--rm",
                "--init",
                f"--net=ns:/proc/{self.pid}/ns/net",
            ]
            if tty:
                cmds.append("-it")
            cmds.append(self.container_image)
        if not isinstance(cmd, str):
            cmds += cmd
        else:
            # Make sure the code doesn't think `cd` will work.
            assert not re.match(r"cd(\s*|\s+(\S+))$", cmd)
            cmds += ["/bin/bash", "-c", cmd]
        return cmds

    def get_cmd_container(self, cmd, sudo=False, tty=False):
        return " ".join(self._get_podman_precmd(cmd, sudo=sudo, tty=tty))

    def popen(self, cmd, **kwargs):
        """
        Creates a pipe with the given `command`.

        Args:
            cmd: `str` or `list` of command to open a pipe with.
            **kwargs: kwargs is eventually passed on to Popen. If `command` is a string
                then will be invoked with `bash -c`, otherwise `command` is a list and
                will be invoked without a shell.

        Returns:
            a subprocess.Popen object.
        """
        if not self.cmd_p:
            return super().popen(cmd, **kwargs)
        skip_pre_cmd = kwargs.get("skip_pre_cmd", True)
        kwargs["skip_pre_cmd"] = True
        cmds = cmd if skip_pre_cmd else self._get_podman_precmd(cmd)
        return self._popen("popen", cmds, async_exec=False, **kwargs)[0]

    async def async_popen(self, cmd, **kwargs):
        if not self.cmd_p:
            return await super().async_popen(cmd, **kwargs)
        skip_pre_cmd = kwargs.get("skip_pre_cmd", True)
        kwargs["skip_pre_cmd"] = True
        cmds = cmd if skip_pre_cmd else self._get_podman_precmd(cmd)
        p, _ = await self._popen("async_popen", cmds, async_exec=True, **kwargs)
        return p

    def cmd_status(self, cmd, **kwargs):
        if not self.cmd_p:
            return super().cmd_status(cmd, **kwargs)
        if tty := kwargs.get("tty", False):
            skip_pre_cmd = False
            del kwargs["tty"]
        else:
            skip_pre_cmd = kwargs.get("skip_pre_cmd", False)
        kwargs["skip_pre_cmd"] = True
        cmds = cmd if skip_pre_cmd else self._get_podman_precmd(cmd, tty)
        cmds = self.cmd_get_cmd_list(cmds)
        return self._cmd_status(cmds, **kwargs)

    async def async_cmd_status(self, cmd, **kwargs):
        if not self.cmd_p:
            return await super().async_cmd_status(cmd, **kwargs)
        if tty := kwargs.get("tty", False):
            skip_pre_cmd = False
            del kwargs["tty"]
        else:
            skip_pre_cmd = kwargs.get("skip_pre_cmd", False)
        kwargs["skip_pre_cmd"] = True
        cmds = cmd if skip_pre_cmd else self._get_podman_precmd(cmd, tty)
        cmds = self.cmd_get_cmd_list(cmds)
        return await self._async_cmd_status(cmds, **kwargs)

    def tmpfs_mount(self, inner):
        # eventually would be nice to support live mounting
        assert not self.container_id
        self.logger.debug("Mounting tmpfs on %s", inner)
        self.extra_mounts.append(f"--mount=type=tmpfs,destination={inner}")

    def bind_mount(self, outer, inner):
        # eventually would be nice to support live mounting
        assert not self.container_id
        self.logger.debug("Bind mounting %s on %s", outer, inner)
        self.extra_mounts.append(f"--mount=type=bind,src={outer},dst={inner}")

    def mount_volumes(self):
        if "volumes" not in self.config:
            return

        args = []
        for m in self.config["volumes"]:
            if isinstance(m, str):
                s = m.split(":", 1)
                if len(s) == 1:
                    args.append("--mount=type=tmpfs,destination=" + m)
                else:
                    spath = s[0]
                    spath = os.path.abspath(
                        os.path.join(
                            os.path.dirname(self.unet.config["config_pathname"]), spath
                        )
                    )
                    args.append(f"--mount=type=bind,src={spath},dst={s[1]}")
                continue
            margs = ["type=" + m["type"]]
            for k, v in m.items():
                if k == "type":
                    continue
                if v:
                    if k in ("src", "source"):
                        v = os.path.abspath(
                            os.path.join(
                                os.path.dirname(self.unet.config["config_pathname"]), v
                            )
                        )
                    margs.append(f"{k}={v}")
                else:
                    margs.append(f"{k}")
            args.append("--mount=" + ",".join(margs))

        # Need to work on a way to mount into live container too
        self.extra_mounts += args

    async def run_cmd(self):
        """Run the configured commands for this node"""

        self.logger.debug("%s: starting container", self.name)
        self.logger.debug(
            "[rundir %s exists %s]", self.rundir, os.path.exists(self.rundir)
        )

        image = self.container_image

        self.container_id = f"{self.name}-{os.getpid()}"
        cmds = [
            get_exec_path_host("podman"),
            "run",
            f"--name={self.container_id}",
            f"--net=ns:/proc/{self.pid}/ns/net",
            f"--hostname={self.name}",
            # We can't use --rm here b/c podman fails on "stop".
            # u"--rm",
        ]

        if self.config.get("init", True):
            cmds.append("--init")

        if self.config.get("privileged", False):
            cmds.append("--privileged")
        else:
            cmds.extend(
                [
                    "--cap-add=SYS_ADMIN",
                    "--cap-add=NET_ADMIN",
                    "--cap-add=NET_RAW",
                ]
            )

        # Add volumes:
        if self.extra_mounts:
            cmds += self.extra_mounts

        # Add environment variables:
        envdict = self.config.get("env", {})
        if envdict is None:
            envdict = {}
        for k, v in envdict.items():
            cmds.append(f"--env={k}='{v}'")

        # Add extra flags from user:
        if "podman" in self.config:
            for x in self.config["podman"].get("extra_args", []):
                cmds.append(x.strip())

        shell_cmd = self.config.get("shell", "/bin/bash")
        cmd = self.config.get("cmd", "").strip()
        if shell_cmd and cmd:
            if not isinstance(shell_cmd, str):
                shell_cmd = "/bin/bash"
            if cmd.find("\n") == -1:
                cmd += "\n"
            cmdpath = os.path.join(self.rundir, "cmd.shebang")
            with open(cmdpath, mode="w+", encoding="utf-8") as cmdfile:
                cmdfile.write(f"#!{shell_cmd}\n")
                cmdfile.write(cmd)
                cmdfile.flush()
            self.cmd_raises_host(f"chmod 755 {cmdpath}")
            cmds += [
                # How can we override this?
                # u'--entrypoint=""',
                f"--volume={cmdpath}:/tmp/cmds.shebang",
                image,
                "/tmp/cmds.shebang",
            ]
        else:
            cmds.append(image)
            if cmd:
                cmds.extend(shlex.split(cmd))

        self.cmd_p = await self.async_popen(
            cmds,
            stdin=subprocess.DEVNULL,
            stdout=open(os.path.join(self.rundir, "cmd.out"), "wb"),
            stderr=open(os.path.join(self.rundir, "cmd.err"), "wb"),
            # start_new_session=True,  # allows us to signal all children to exit
            skip_pre_cmd=True,
        )

        self.logger.debug("%s: async_popen => %s", self, self.cmd_p.pid)

        # ---------------------------------------
        # Now let's wait until container shows up
        # ---------------------------------------
        timeout = Timeout(30)
        while self.cmd_p.returncode is None and not timeout.is_expired():
            o = await self.async_cmd_raises_host(
                f"podman ps -q -f name={self.container_id}"
            )
            if o.strip():
                break
            elapsed = int(timeout.elapsed())
            if elapsed <= 3:
                await asyncio.sleep(0.1)
            else:
                self.logger.info("%s: run_cmd taking more than %ss", self, elapsed)
                await asyncio.sleep(1)
        if self.cmd_p.returncode:
            self.logger.error(
                "%s: run_cmd failed: %s", self, await acomm_error(self.cmd_p)
            )
        assert self.cmd_p.returncode is None

        self.logger.info("%s: started container", self.name)

        return self.cmd_p

    def cmd_completed(self, future):
        try:
            n = future.result()
            self.container_id = None
            self.logger.info("%s: contianer cmd completed result: %s", self, n)
        except asyncio.CancelledError:
            # Should we stop the container if we have one?
            self.logger.debug("%s: container cmd.wait() canceled", future)

    async def async_delete(self):
        if type(self) == L3ContainerNode:  # pylint: disable=C0123
            self.logger.info("%s: container async deleting", self.name)
        else:
            self.logger.debug("%s: container async delete", self.name)

        if self.container_id:
            if self.cmd_p and (rc := self.cmd_p.returncode) is None:
                rc, o, e = await self.async_cmd_status_host(
                    [get_exec_path_host("podman"), "stop", self.container_id]
                )
            if rc and rc < 128:
                self.logger.warning(
                    "%s: podman stop on cmd failed: %s", self, cmd_error(rc, o, e)
                )
            # now remove the container
            rc, o, e = await self.async_cmd_status_host(
                [get_exec_path_host("podman"), "rm", self.container_id]
            )
            if rc:
                self.logger.warning(
                    "%s: podman rm failed: %s", self, cmd_error(rc, o, e)
                )
            else:
                # It's gone
                self.cmd_p = None
        # From here on out we do not want our cmd_* overrides to take effect.
        await super().async_delete()

    def delete(self):
        asyncio.run(L3ContainerNode.async_delete(self))


class Munet(BaseMunet):
    """
    Munet.
    """

    def __init__(self, rundir=None, **kwargs):
        super().__init__(**kwargs)
        self.rundir = rundir if rundir else tempfile.mkdtemp(prefix="unet")
        self.cmd_raises(f"mkdir -p {self.rundir} && chmod 755 {self.rundir}")

    def add_l3_link(self, node1, node2, c1, c2):
        """Add a link between switch and node or 2 nodes."""
        isp2p = False

        if node1.name in self.switches:
            assert node2.name in self.hosts
        elif node2.name in self.switches:
            assert node1.name in self.hosts
            node1, node2 = node2, node1
            c1, c2 = c2, c1
        else:
            # p2p link
            assert node1.name in self.hosts
            assert node1.name in self.hosts
            isp2p = True

        if "name" not in c1:
            c1["name"] = node1.get_next_intf_name()
        if1 = c1["name"]

        if "name" not in c2:
            c2["name"] = node2.get_next_intf_name()
        if2 = c2["name"]

        super().add_link(node1, node2, if1, if2)

        if isp2p:
            node1.set_p2p_addr(c1, node2, c2)
        else:
            node2.set_lan_addr(c2, node1)

    def add_l3_node(self, name, config, **kwargs):
        """Add a node to munet."""

        if "image" in config:
            cls = L3ContainerNode
        else:
            cls = L3Node
        return super().add_host(name, cls=cls, unet=self, config=config, **kwargs)

    def add_l3_switch(self, name, config, **kwargs):
        """Add a switch to munet."""

        return super().add_switch(name, cls=L3Bridge, config=config, **kwargs)

    async def run(self):
        tasks = []
        run_nodes = [x for x in self.hosts.values() if hasattr(x, "run_cmd")]
        await asyncio.gather(*[x.run_cmd() for x in run_nodes])
        for node in run_nodes:
            task = asyncio.create_task(node.cmd_p.wait(), name=f"Node-{node.name}-cmd")
            # utask.add_done_callback(node.cmd_completed)
            tasks.append(task)
        return tasks