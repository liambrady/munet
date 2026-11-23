# SPDX-License-Identifier: GPL-2.0-or-later
"""Initialize the test guest after the kind is ready."""

from pathlib import Path

assert Path("/root/kind-ready").exists()
Path("/root/foo").touch()
