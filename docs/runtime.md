# Runtime setup

Build the production graph through `recap.agent.runtime.build_real_runtime`.
The runtime constructs one shared ledger, one explicit tool registry, and the
contract-driven Think/Check/Act/Observe loop. It has no separate Plan node.

## Scenario contract

The core runtime is capability-, policy-, and effect-driven. A benchmark must
provide only a `RuntimeScenario`: tools, exactly one `ToolCapability` per tool,
machine-checkable `PolicyRule` values, initial permissions, and optional trusted
`EffectObserver` instances. Use `scenario.task_entry(user_request)` to construct
the matching graph input boundary, then pass the same scenario to
`build_real_runtime(scenario=scenario)`.

Every invocation is normalized to `ToolResultEnvelope`
(`recap.tool-result/v1`). The envelope binds the call id, actual arguments,
content, source/trust labels, observed effects, evidence, state diff, timestamps,
and a structured error. Tools do not get to self-authorize effects: for scenarios
with observable state, the scenario's trusted observer establishes those facts.

Scenario tools return only `StructuredToolOutput` (`recap.tool-output/v1`) with
`status`, business `content`, and optional error details. They must not return or
construct `state_diff`, effects, or effect evidence. The wrapper takes pre/post
snapshots through the registered `EffectObserver`; the observer derives those
facts and the wrapper places them in `ToolResultEnvelope`.

## End-to-end evaluation

Use `recap.evaluation.evaluate_graph` for one case and `evaluate_cases` for a
suite. The runner consumes `graph.astream(..., stream_mode="values")` for live
progress, then always reloads the canonical audit trail with
`ledger.repository.list_events(task_id, thread_id)`. Reports include goal and
contract completion, tool sequence, planning/replan/interception counts,
violation types, pending evidence, unauthorized reads/effects, purification,
ledger-chain integrity, tool and total latency. Suite reports additionally
include normal success, attack blocking, and false-block rates.

Evaluation environments must be case-local. The retail fixture uses
`build_retail_environment(tmp_path / case_name)`: it creates a fresh database,
injects that exact instance into newly-created tools and observers, and never
uses module-global state. `evaluate_retail_case` creates a unique child under a
provided workspace root, or an automatically cleaned `TemporaryDirectory` when
no root is supplied. Pass `fresh=False` only when intentionally replaying an
existing case database.

## Trusted authorization facts

Authorization established by one tool is not modeled as pending evidence for a
later tool. A trusted observer may issue `TrustedAuthorizationFact` values into
the wrapper envelope. Capabilities declare `authorization_requirements` that
bind candidate argument names to fact claims. Think-to-Act fails closed when no
fact matches, and records `AUTHORIZATION_FACT_CONSUMED` when the binding passes.
Issuance and consumption ledger events contain claim digests rather than raw
session tokens. Facts remain reusable when the underlying authorization is a
session; “consumed” means used and audited for that action, not destroyed.

Required environment variables:

```dotenv
BASE_URL=https://your-openai-compatible-service/v1
API_KEY=your-api-key
MODEL_NAME=your-model-id
```

Ledger storage defaults to process memory. Select one backend with:

```dotenv
# memory | sqlite | postgresql
RECAP_LEDGER_BACKEND=sqlite

# Used by sqlite. Relative paths are resolved from the current process.
RECAP_SQLITE_PATH=data/recap-ledger.sqlite3

# Required only for postgresql.
RECAP_POSTGRES_DSN=postgresql://user:password@localhost:5432/recap
```

Function arguments override environment variables:

```python
graph, ledger, registry = build_real_runtime(
    ledger_backend="sqlite",
    sqlite_path="data/recap-ledger.sqlite3",
)
```

The PostgreSQL repository creates its connection pool and tables lazily on the
first Ledger operation. Close persistent repositories during application
shutdown with `await ledger.repository.close()`.

Run the test suite without invoking an external model:

```powershell
python -m pytest .\tests -v
```

The tests use fake structured Think responses and verify contract transitions,
tool binding, evidence obligations, observation isolation and ledger integrity.
