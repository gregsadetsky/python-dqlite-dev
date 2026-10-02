# testlib

Shared test utilities for the four sibling packages
(`python-dqlite-wire`, `python-dqlite-client`, `python-dqlite-dbapi`,
`sqlalchemy-dqlite`). The testlib provides:

- `TestClusterControl` — drives the test cluster deterministically
  (transfer leadership, observe convergence) using
  `ClusterClient.transfer_leadership` from `python-dqlite-client`.
- A `cluster_control` pytest fixture wrapping the above against the
  env-configured cluster.

Not pip-installable — `python-dqlite-dev` is a development-only repo.
Consumer packages reach the testlib by adding it to `sys.path` from
their own `tests/conftest.py`.

## Bootstrap snippet for consumer packages

In each consumer's `tests/conftest.py`:

```python
import sys
from pathlib import Path

# Add python-dqlite-dev's testlib to sys.path so tests can import
# from ``dqlitetestlib``. Sibling-repo path: tests are at
# <pkg>/tests/conftest.py; python-dqlite-dev is at <workspace>/python-dqlite-dev.
_TESTLIB = (
    Path(__file__).resolve().parent.parent.parent
    / "python-dqlite-dev"
    / "testlib"
)
if _TESTLIB.exists() and str(_TESTLIB) not in sys.path:
    sys.path.insert(0, str(_TESTLIB))

# Re-export the cluster_control fixture so tests can request it by
# name without an explicit import.
pytest_plugins = ["dqlitetestlib.fixtures"]
```

Tests then write:

```python
from dqlitetestlib import TestClusterControl

async def test_pool_recovers_after_leader_flip(
    cluster_control: TestClusterControl,
) -> None:
    result = await cluster_control.force_leader_flip()
    assert result.leader_after == result.target.address
    # ... assert the pool re-routes to the new leader ...
```

## Network faults

`network_faults` (fixture) / `NetworkFaults` add iptables rules to the
cluster's network namespace (the nodes use `network_mode: host`, so it is
the docker host's) from a privileged helper container,
`dqlitetestlib-faults` (`alpine:3.20` with `iptables` and `iproute2`
installed on first use; needs network access to pull it). The nodes need
no extra capabilities and the compose file is unchanged.

- `isolate_from_followers(leader_port)`: one-way partition. The followers'
  connections to the leader are dropped, so the leader cannot hear their
  acknowledgements, cannot commit, and steps down; the followers elect a
  new leader (about 10 s on this cluster).
- `drop_replies(server_port, client_port)`: the server still receives and
  executes requests on that one client connection, but the replies are
  dropped, so the client times out on a write that was applied.

Every rule carries the comment `dqlitetestlib-fault`. The context managers
remove their rules on exit, also when the test raises, and the fixture
calls `clear()` (remove every tagged rule) after each test.

```python
from dqlitetestlib import NetworkFaults

async def test_commit_reply_lost(network_faults: NetworkFaults) -> None:
    with network_faults.drop_replies(server_port, client_port):
        ...  # the request runs on the server, the reply never arrives
```

## Layout

```
testlib/
└── dqlitetestlib/
    ├── __init__.py        # re-exports TestClusterControl, NetworkFaults + fixtures
    ├── cluster_control.py # the class itself
    ├── faults.py          # NetworkFaults: partitions, dropped replies
    └── fixtures.py        # pytest fixtures
```

## What it does NOT do (yet)

- **No container kill/restart primitives.** The current
  `TestClusterControl` only flips leadership via
  `transfer_leadership` — the graceful step-down path. Tests that
  need to model a hard node failure (kill the leader, observe pool
  invalidation) need a `kill_node` / `start_node` primitive that
  shells out to docker. Coming when a test actually needs it.
- **No state snapshot/restore.** Tests that flip the leader leave
  the cluster with a different leader than they started; subsequent
  tests must tolerate that, or restore via
  `transfer_leadership_to(original_leader_id)` in their own
  teardown.
