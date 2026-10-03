"""Network faults on the test cluster: one-way partitions and dropped replies.

The cluster's nodes run with ``network_mode: host``, so they share one
network namespace (the docker host's). These helpers add iptables rules
there from a privileged helper container on the host network; the nodes
themselves need no extra capabilities. Every rule carries the
:data:`RULE_TAG` comment, and :meth:`NetworkFaults.clear` removes all of
them, so a failed test cannot leave the cluster partitioned.
"""

from __future__ import annotations

import contextlib
import shlex
import subprocess
from collections.abc import Iterator
from typing import Final

RULE_TAG: Final[str] = "dqlitetestlib-fault"

_HELPER: Final[str] = "dqlitetestlib-faults"
_IMAGE: Final[str] = "alpine:3.20"


def _docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def _node_port(n: int) -> int:
    return 9000 + n


class NetworkFaults:
    """iptables rules in the cluster's network namespace, tagged and removable."""

    def __init__(self, nodes: int = 3) -> None:
        self._nodes = nodes
        self._ready = False

    def _ensure_helper(self) -> None:
        if self._ready:
            return
        if not _docker("ps", "-q", "-f", f"name=^{_HELPER}$"):
            subprocess.run(["docker", "rm", "-f", _HELPER], capture_output=True, check=False)
            _docker(
                "run", "-d", "--rm", "--name", _HELPER,
                "--privileged", "--network", "host", "--pid", "host",
                _IMAGE, "sleep", "infinity",
            )  # fmt: skip
            _docker("exec", _HELPER, "apk", "add", "--no-cache", "-q", "iptables", "iproute2")
        self._ready = True

    def _iptables(self, *args: str) -> str:
        self._ensure_helper()
        return _docker("exec", _HELPER, "iptables", *args)

    def rules(self) -> list[str]:
        """The tagged rules currently installed, in ``iptables -S`` form."""
        return [line for line in self._iptables("-S", "INPUT").splitlines() if RULE_TAG in line]

    def clear(self) -> int:
        """Remove every tagged rule; return how many were removed."""
        removed = 0
        for line in self.rules():
            spec = shlex.split(line)
            if spec[:2] == ["-A", "INPUT"]:
                self._iptables("-D", *spec[1:])
                removed += 1
        return removed

    def _drop(self, sport: int, dport: int) -> list[str]:
        rule = [
            "INPUT", "-p", "tcp", "-s", "127.0.0.1", "-d", "127.0.0.1",
            "--sport", str(sport), "--dport", str(dport),
            "-m", "comment", "--comment", RULE_TAG, "-j", "DROP",
        ]  # fmt: skip
        self._iptables("-I", *rule)
        return rule

    def _undrop(self, rule: list[str]) -> None:
        self._iptables("-D", *rule)

    def _undrop_all(self, rules: list[list[str]]) -> None:
        """Remove each rule, still trying the others if one removal fails."""
        failed: BaseException | None = None
        for rule in reversed(rules):
            try:
                self._undrop(rule)
            except BaseException as exc:
                failed = failed or exc
        if failed is not None:
            raise failed

    def _pids(self, n: int) -> set[int]:
        lines = _docker("top", f"dqlite-node{n}", "-eo", "pid").splitlines()[1:]
        return {int(line.strip()) for line in lines if line.strip()}

    def follower_connections(self, leader_port: int) -> set[int]:
        """Source ports of the connections the followers opened to the leader's port."""
        self._ensure_helper()
        pids: set[int] = set()
        for n in range(1, self._nodes + 1):
            if _node_port(n) != leader_port:
                pids |= self._pids(n)
        ports: set[int] = set()
        for line in _docker("exec", _HELPER, "ss", "-tnpH").splitlines():
            parts = line.split()
            if len(parts) < 6 or parts[4] != f"127.0.0.1:{leader_port}":
                continue
            if any(f"pid={pid}," in line for pid in pids):
                ports.add(int(parts[3].rsplit(":", 1)[1]))
        return ports

    @contextlib.contextmanager
    def isolate_from_followers(self, leader_port: int) -> Iterator[set[int]]:
        """One-way partition: the leader stops hearing its followers; it can still send.

        It cannot get acknowledgements, so it cannot commit and steps down while
        the followers, which still hear it until the election timeout, elect a new
        leader among themselves. Yields the blocked source ports.
        """
        ports = self.follower_connections(leader_port)
        if len(ports) < self._nodes - 1:
            raise RuntimeError(
                f"expected a connection from each follower to {leader_port}, got {ports}"
            )
        rules: list[list[str]] = []
        try:
            for sport in sorted(ports):
                rules.append(self._drop(sport, leader_port))
            yield ports
        finally:
            self._undrop_all(rules)

    @contextlib.contextmanager
    def drop_replies(self, server_port: int, client_port: int) -> Iterator[None]:
        """The server keeps receiving on this one connection, but its replies are dropped."""
        rule = self._drop(server_port, client_port)
        try:
            yield
        finally:
            self._undrop(rule)
