# ReCAP project structure

The repository uses one Python source root: `src/recap`. Generated metadata and
virtual environments do not belong in that tree.

```text
EndToEndAgent/
|-- docs/                   # Architecture and integration notes
|-- examples/               # Runnable examples
|-- src/recap/
|   |-- agent/
|   |   |-- graph.py        # LangGraph topology and node stubs
|   |   `-- state.py        # Shared graph state and routing types
|   |-- contracts/
|   |   `-- models.py       # Runtime contract aggregate and transitions
|   |-- ledger/
|   |   |-- models.py       # Canonical hash-chained events
|   |   `-- service.py      # Repository protocol and in-memory service
|   |-- integration.py      # Contract/ledger bridge functions
|   `-- schemas.py          # Intent, action, observation, and violation schemas
`-- tests/                  # Tests that do not require an external LLM
```

## Ownership rules

- `schemas.py` contains transport and audit schemas shared across subsystems.
- `contracts` owns the executable per-round contract lifecycle.
- `ledger` owns append-only event storage and chain verification.
- `agent/state.py` is the only graph-state declaration.
- `agent/graph.py` owns graph topology. Node implementations can be extracted
  later when they contain real behavior; empty placeholder modules are avoided.
- `integration.py` translates graph decisions into contract transitions and
  ledger events without owning either domain model.

## Next implementation boundary

The graph nodes are currently documented stubs. Implement the Think -> Act,
Act -> Observe, and Observe -> Think checks before adding API, worker,
persistence, recovery, security, or observability packages. Add each package
only when its first concrete implementation is introduced.

The in-memory ledger is suitable for tests and local development. A future
database repository must append an event and lock/update the chain head in one
transaction to prevent competing hashes.
