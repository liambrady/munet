.. SPDX-License-Identifier: GPL-2.0-or-later
..
.. November 26 2022, Christian Hopps <chopps@labn.net>
..
.. Copyright (c) 2022, LabN Consulting, L.L.C.
..

.. _munet-config:

Configuration
=============

Munet configuration is modeled with YANG. The YANG module definition can be
found XXX`here`.

Variables
---------

Munet defines a few variables for use within both any munet configuration file
and the enviornment namespaces of any munet node.

Configuration Files
^^^^^^^^^^^^^^^^^^^

The following variables are expanded with their dynamic values when munet
interprets the configuration.

  ``%CONFIGDIR%``
    Expands to the absolute path to the directory containing the config file.
    This variable is often useful for mounting configuration directories and
    files.

  ``%NAME%``
    Expands to the name of the node owning the config item this variable is used
    in.

  ``%RUNDIR%``
    Expands to the absolute path to the runtime directory of the node owning
    the config item this variable is used in. This directory is often useful for
    mounting runtime directories (e.g., log directories) in the node namespace.

.. warning::
  Some configuration options do not support the full range of configuration
  variables. In order to determine whether a variable will be expanded or not,
  please check the config's description within the YANG module definition.


Environment Variables
^^^^^^^^^^^^^^^^^^^^^

The following enviornment variables are made available to any command executed
within a munet node or interactive terminal opened within a munet node. These
variables should not be overwritten since they may be used to assist munet in
cleaning up.

  ``MUNET_PID``
    The PID of the parent munet process.

  ``MUNET_NODENAME``
    The name of the target munet node.

Some extra variables are also set *only* in interactive terminals to make
availible the same variables present within the regular munet config. (i.e.
see the start of the ``Variables`` section.)

  ``RUNDIR``
    Same as ``%RUNDIR%``.

  ``NODENAME``
    Same as ``%NAME%``.

  ``CONFIGDIR``
    Same as ``%CONFIGDIR%``.


Topology
--------

The topology section defines the networks and nodes that make up the topology
along with a few global topology options.

.. pyang labn-munet-config.yang -f tree --tree-path=/topology

Tree diagram for topology config::

   +--rw topology
   |  +--rw dns-network?              -> ../networks/name
   |  +--rw ipv6-enable?              boolean
   |  +--rw networks-autonumber?      boolean
   |  +--rw loopbacks-autonumber?     boolean
   |  +--rw initial-setup-cmd?        string
   |  +--rw initial-setup-host-cmd?   string
   |  +--rw networks* [name]
   |     ... described in subsection
   |  +--rw nodes* [name]
   |     ... described in subsection


Networks
^^^^^^^^

.. pyang labn-munet-config.yang -f tree --tree-path=/topology/networks

Tree diagram for network config::

  +--rw topology
     +--rw networks* [name]
        +--rw name                  string
        +--rw ip?                   string
        +--rw ipv6?                 string
        +--rw external?             boolean
        +--rw delay?                uint64
        +--rw jitter?               uint64
        +--rw jitter-correlation?   decimal64
        +--rw loss?                 uint64
        +--rw loss-correlation?     decimal64
        +--rw rate
        |  +--rw rate?    number64
        |  +--rw limit?   number64
        |  +--rw burst?   number64
        +--rw connections* [to]
           +--rw to                    string
           +--rw name?                 string
           +--rw remote-name?          string
           +--rw delay?                uint64
           +--rw jitter?               uint64
           +--rw jitter-correlation?   decimal64
           +--rw loss?                 uint64
           +--rw loss-correlation?     decimal64
           +--rw rate
              +--rw rate?    number64
              +--rw limit?   number64
              +--rw burst?   number64

The ``connections`` configuration is entirely optional, and is present to allow
overriding default network quality parameters (e.g., ``delay``, ``jitter``,
etc). If no override is required it is enough for the nodes to specify their
connection to the network.


Nodes
^^^^^

.. pyang labn-munet-config.yang -f tree --tree-path=/topology/nodes

Tree diagram for node config::

  +--rw topology
     +--rw nodes* [name]
        +--rw id?                  uint32
        +--rw kind?                -> ../../../kinds/name
        +--rw ip*                  string
        +--rw ipv6*                string
        +--rw cap-add*             string
        +--rw cap-remove*          string
        +--rw cmd?                 string
        +--rw cmd-file?            string
        +--rw container-cmd?       string
        +--rw cleanup-cmd?         string
        +--rw ready-cmd?           string
        +--rw ready-timeout?       uint32
        +--rw image?               string
        +--rw hostnet?             boolean
        +--rw server?              string
        +--rw server-port?         uint16
        +--rw ssh-identity-file?   string
        +--rw ssh-user?            string
        +--rw ssh-password?        string
        +--rw qemu
            ... intentionally truncated
        +--rw connections* [to]
            ... described in subsections
        +--rw env* [name]
        |  +--rw name     string
        |  +--rw value?   string
        +--rw gdb-cmd?             string
        +--rw gdb-target-cmds*     string
        +--rw gdb-run-cmds*        string
        +--rw init?                union
        +--rw mounts* [destination]
            ... described in subsections
        +--rw name                 string
        +--rw podman
        |  +--rw extra-args*   string
        +--rw privileged?          boolean
        +--rw shell?               union
        +--rw volumes*             string

A particularly useful config option for any node is to set the ``cmd``. The
``cmd`` is a startup command that will run each time when the node is
started by munet. It can be used to setup the runtime directory,
automatically run utilities, etc. ::

    topology:
     nodes:
       - name: h1
	 cmd: |
	   echo "foo" > foo.txt
	   cat ./foo.txt

The default interpreter for this is ``/bin/bash``, however, separate interpreters
can be used when ``shell`` is set to the interpreter path (e.g. ``/usr/bin/python3``)
Alternatively, the ``cmd`` can be treated as basic console input if ``shell`` is set
to `false`.

When ``cmd-file`` is set to a path, the startup command is instead read from the
specified file (and the configured ``cmd`` ignored.) A best effort attempt is made
to use the same interpreter as specified by the file's shebang (again, defaulting
to ``/bin/bash`` if none is found). Setting ``shell`` overrides this behavior to use
a specific configured interpreter instead.

Lifecycle commands
""""""""""""""""""

By default a node has one lifecycle stage with the final values after merging
its kind and node configuration. Set ``merge: [commands]`` on a kind to run the
effective kind's hooks in one stage, followed by the node's own hooks in a second
stage. Each stage uses its own ``shell`` or the backend default. The stage shell
only applies to lifecycle hooks. Ordinary CLI and programmatic commands keep
their existing execution behavior.

.. code-block:: yaml

   kinds:
     - name: router
       merge: [commands]
       cmd: start-kind-service
       ready-cmd: check-kind-service
       cleanup-cmd: stop-kind-service

   topology:
     nodes:
       - name: r1
         kind: router
         cmd: start-node-service
         ready-cmd: check-node-service
         cleanup-cmd: stop-node-service

A stage reaches readiness when its ``ready-cmd`` returns zero, or immediately
after startup dispatch when no readiness check is configured. Each check has
its own ``ready-timeout``, which defaults to 30 seconds. Startup exit status is
reported for diagnostics. Namespace and container stage commands may continue
running while subsequent stages start. Nodes advance independently. QEMU runs
its hooks synchronously through the guest console, and its first-time disk
initialization commands run before the lifecycle stages. Guest readiness checks
require a console that can report command exit status.

A stage without ``cmd`` or ``cmd-file`` can still check readiness and run cleanup.
At teardown, munet runs ``cleanup-cmd`` for each entered stage in reverse order,
while the namespace, container, or VM is still available. Stage stdout and
stderr for namespace and container execution are written to ``cmd.out`` and
``cmd.err`` in single-stage mode, or to ``cmd.N.out`` and ``cmd.N.err`` in composed
mode, where N is the one-based stage number. QEMU uses its console logs.

Podman boot
"""""""""""

Set ``container-cmd`` to choose the command passed to ``podman run``. It uses the
merged node ``shell`` and the usual kind-to-node precedence. An explicitly empty
``container-cmd: ""`` selects the image default and leaves all lifecycle commands
available to run via ``podman exec``.

For compatibility, if ``container-cmd`` is omitted, munet uses the first stage
with a configured startup command for container boot. This selects the
effective kind's command before the node's in composed mode and the final merged
command in single-stage mode. A configured ``cmd-file`` takes precedence over
``cmd`` in each stage. Munet warns when it uses this fallback and consumes the
selected startup command once, preserving that stage's readiness and cleanup
hooks. With no eligible startup command, the image default is used. Relative
Podman ``cmd-file`` paths are resolved from the topology configuration directory.
Container boot logs are written to ``container.out`` and ``container.err``.

Container launch waits for Podman to accept exec requests. A daemon started by
the boot command may still be initializing when the first lifecycle stage
starts. To gate a later stage on that daemon, use an earlier readiness-only
stage with ``ready-cmd``.

Connections
"""""""""""

.. pyang labn-munet-config.yang -f tree --tree-path=/topology/nodes/connections

Tree diagram for node connections::

   +--rw topology
   |  +--rw nodes* [name]
   |     +--rw connections* [to]
   |     |  +--rw to                    string
   |     |  +--rw ip?                   string
   |     |  +--rw name?                 string
   |     |  +--rw hostintf?             string
   |     |  +--rw physical?             string
   |     |  +--rw remote-name?          string
   |     |  +--rw driver?               string
   |     |  +--rw delay?                uint64
   |     |  +--rw jitter?               uint64
   |     |  +--rw jitter-correlation?   decimal64
   |     |  +--rw loss?                 uint64
   |     |  +--rw loss-correlation?     decimal64
   |     |  +--rw rate
   |     |     +--rw rate?    number64
   |     |     +--rw limit?   number64
   |     |     +--rw burst?   number64


Mounts
""""""

.. pyang labn-munet-config.yang -f tree --tree-path=/topology/nodes/mounts

Tree diagrame for node mounts::

   +--rw topology
   |  +--rw nodes* [name]
   |     +--rw mounts* [destination]
   |     |  +--rw destination    string
   |     |  +--rw source?        string
   |     |  +--rw size?          string
   |     |  +--rw type?          string

Two main types of mounts are supported: `bind` and `tempfs` mounts. Use the `bind`
mount to bind the `source` file/directory to the specified `destination` within
the munet node. Alternatively, use the `tmpfs` mount to create a temporary file
system within the munet node at the specified `destination` of size `size`.

.. note::

   The `bind` mount for Qemu (VM) nodes is tightly bound with the 9p network
   file system. For guests that do not support the 9p file system, an alternative
   `usb` type is supported that emulates a directory as a USB drive.

   Note that using the `usb` type might require additional configuration within
   the guest operating system, such as disabling `usbguard` in some RHEL systems.

Kinds
-----

A kind configuration is the same as ``node:`` config and allows for specifying
common node configuration for a "kind" of node. By specifying a ``kind:`` config
type for a node, the node will inherits all the config values from that kind.

The following example illustrates creating an **ubuntu-container** kind which
specifies a container image and a tmpfs mount. This new kind is used to create 3
nodes, **u1**, **u2**, and **u3**.

.. code-block:: yaml

  topology:
    nodes:
      - name: u1
        kind: ubuntu-container
      - name: u2
        kind: ubuntu-container
      - name: u3
        kind: ubuntu-container
    # ...
  kinds:
    - name: ubuntu-container
      image: docker.io/ubuntu
      mounts:
        - type: tmpfs
          tmpfs-size: 512M
          destination: /mytmp


CLI
---

.. pyang labn-munet-config.yang -f tree --tree-path=/cli

Tree diagram for CLI config::

   +--rw cli
      +--rw commands* [name]
         +--rw exec?          string
         +--rw exec-kind* [kind]
         |  +--rw kind    string
         |  +--rw exec?   string
         +--rw format?        string
         +--rw help?          string
         +--rw interactive?   boolean
         +--rw kinds*         -> ../../../kinds/name
         +--rw name           string
         +--rw new-window?    boolean
         +--rw top-level?     boolean

The following example illustrates creating 2 CLI commands.

.. code-block:: yaml
   :caption: An example of defining 2 CLI commands

   cli:
     commands:
       - name: ""
         exec: "vtysh -c '{}'"
         format: "[ROUTER ...] COMMAND"
         help: "execute vtysh COMMAND on the router[s]"
         kinds: ["frr"]

       - name: "vtysh"
         exec: "/usr/bin/vtysh"
         format: "vtysh ROUTER [ROUTER ...]"
         new-window: true
         kinds: ["frr"]

The first CLI command, because it has an empty :yaml:`name:`, is a default command. The
default command is executed if the user entered command line does not match any
other defined CLI commands. In this case the command text is inserted into the
command :code:`vtysh -c 'user-entered-value'` and is executed on the ROUTER[s]
(node[s]) the user specifies or all nodes if no ROUTER (node names) are
supplied.

Note the use of the **kinds:** config. This restricts the command to only
running on nodes of the specified kinds. In the example above the commands will
only run on nodes which are defined as **frr** kind.

The second command is a window creating command. For each ROUTER (node)
specified a window will be opened using the users window system (**tmux**,
**screen**, or **X11**). In this case the command that will be run in each
window is an *FRR* console (**vtysh**).
