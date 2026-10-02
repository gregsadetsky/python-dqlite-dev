"""Pytest fixtures wrapping :class:`TestClusterControl`.

Imported by consumer packages that have already arranged for
``dqlitetestlib`` to be on ``sys.path`` (the conftest.py bootstrap
documented in ``python-dqlite-dev/testlib/README.md``).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from dqlitetestlib.cluster_control import TestClusterControl
from dqlitetestlib.faults import NetworkFaults


def _addresses_from_env() -> list[str]:
    """Read ``DQLITE_TEST_CLUSTER_NODES`` (comma-separated) with the
    canonical 3-node-on-localhost default that matches the
    python-dqlite-dev cluster.
    """
    raw = os.environ.get(
        "DQLITE_TEST_CLUSTER_NODES",
        "localhost:9001,localhost:9002,localhost:9003",
    )
    return [s.strip() for s in raw.split(",") if s.strip()]


@pytest.fixture
def cluster_control() -> TestClusterControl:
    """Yield a :class:`TestClusterControl` bound to the env-configured
    cluster (``DQLITE_TEST_CLUSTER_NODES``).

    Tests that mutate cluster state (leader flip, future kill/restart
    primitives) should restore the cluster's invariants in their own
    teardown — this fixture does not snapshot or revert state.
    """
    return TestClusterControl(_addresses_from_env())


@pytest.fixture
def network_faults() -> Iterator[NetworkFaults]:
    """Yield :class:`NetworkFaults`; every rule it added is removed afterwards,
    even if the test failed, and the cluster is left without tagged rules."""
    faults = NetworkFaults()
    try:
        yield faults
    finally:
        faults.clear()
