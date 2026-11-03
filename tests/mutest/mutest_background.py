"""Test background command steps and send/expect functionality."""

import re
import shlex
import uuid

from munet.mutest.userapi import background_match_step
from munet.mutest.userapi import background_match_step_json
from munet.mutest.userapi import background_start
from munet.mutest.userapi import background_step
from munet.mutest.userapi import background_step_json
from munet.mutest.userapi import section
from munet.mutest.userapi import step
from munet.mutest.userapi import test_step
from munet.mutest.userapi import wait_step


section("Test background_step and stdout/stderr capture")
pid = background_start(
    "r1",
    "echo 'stdout message'; echo 'stderr message' >&2; touch /tmp/mutest-bg-done",
)
wait_step(
    "r1",
    "test -f /tmp/mutest-bg-done && echo READY",
    "READY",
    timeout=5,
    interval=0.1,
)
step("r1", "rm -f /tmp/mutest-bg-done")
out = background_step(pid)
test_step(
    "stdout message" in out and "stderr message" in out,
    "Verify background stdout/stderr capture",
    "r1",
)

# An ending step consumes the PID. A second ending step is invalid usage.
try:
    background_step(pid)
except KeyError:
    test_step(True, "Reject a previously consumed background PID", "r1")
else:
    test_step(False, "Reject a previously consumed background PID", "r1")



section("Test positive background_match_step calls")
step("r1", "rm -f /tmp/mutest-bg-match-done")
pid = background_start(
    "r1",
    "echo 'Neighbor 10.0.0.1 established'; touch /tmp/mutest-bg-match-done",
)
wait_step(
    "r1",
    "test -f /tmp/mutest-bg-match-done && echo READY",
    "READY",
    timeout=5,
    interval=0.1,
)
step("r1", "rm -f /tmp/mutest-bg-match-done")
result = background_match_step(
    pid,
    r"Neighbor (\S+) established",
    "Match background command output",
)
test_step(
    result is not None,
    "Verify background_match_step returns a match",
    "r1",
)

pid = background_start(
    "r1",
    "printf 'first line\\nsecond line\\n'; touch /tmp/mutest-bg-match-done",
)
wait_step(
    "r1",
    "test -f /tmp/mutest-bg-match-done && echo READY",
    "READY",
    timeout=5,
    interval=0.1,
)
step("r1", "rm -f /tmp/mutest-bg-match-done")
success, matched = background_match_step(
    pid,
    "first line\nsecond line",
    "Match exact multiline background output",
    exact_match=True,
)
test_step(
    success and matched == "first line\nsecond line",
    "Return the exact matching text",
    "r1",
)



section("Test negative (expect_fail) background_match_step calls")
pid = background_start(
    "r1",
    "printf 'interface UP\\n'; touch /tmp/mutest-bg-match-done",
)
wait_step(
    "r1",
    "test -f /tmp/mutest-bg-match-done && echo READY",
    "READY",
    timeout=5,
    interval=0.1,
)
step("r1", "rm -f /tmp/mutest-bg-match-done")
success, matched = background_match_step(
    pid,
    "interface DOWN",
    "Verify that background output does not contain DOWN",
    expect_fail=True,
)
test_step(
    success and matched is None,
    "Return success for the expected absence of a regex match",
    "host1",
)





# XXX lbrady: rest is TODO


section("Test background commands across targets and sections")
r1_pid = start_ready("r1", "printf 'router-background\\n'")
host_pid = start_ready("host1", "printf 'host-background\\n'")

section("Complete background commands started in a previous section")
background_match_step(host_pid, "host-background", "Match host1 command")
background_match_step(r1_pid, "router-background", "Match r1 command")


section("Test background_step_json")
json_output = '{"name":"background","ok":true,"count":3}'
pid = start_ready("r1", f"printf '%s\\n' {shlex.quote(json_output)}")
js = background_step_json(pid)
test_step(
    js == {"name": "background", "ok": True, "count": 3},
    "Parse background JSON output",
    "r1",
)

pid = start_ready("r1", "printf 'not json\\n'")
js = background_step_json(pid)
test_step(js == {}, "Return an empty dict for invalid background JSON", "r1")


section("Test positive background_match_step_json calls")
pid = start_ready("r1", f"printf '%s\\n' {shlex.quote(json_output)}")
success, matched = background_match_step_json(
    pid,
    {"name": "background"},
    "Match a subset of a background JSON object",
)
test_step(
    success and matched == {"name": "background", "ok": True, "count": 3},
    "Return the background JSON object on a successful match",
    "r1",
)

pid = start_ready("host1", f"printf '%s\\n' {shlex.quote(json_output)}")
success, _ = background_match_step_json(
    pid,
    {"name": "background", "ok": True, "count": 3},
    "Match exact background JSON",
    exact_match=True,
)
test_step(success, "Honor exact_match for background JSON", "host1")


section("Test negative (expect_fail) background_match_step_json calls")
pid = start_ready("r1", f"printf '%s\\n' {shlex.quote(json_output)}")
success, diff = background_match_step_json(
    pid,
    {"ok": False},
    "Verify background JSON does not match an incorrect value",
    expect_fail=True,
)
test_step(
    success and bool(diff),
    "Return a JSON difference when the expected negative match succeeds",
    "r1",
)
