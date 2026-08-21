# Runtime setup

Build the production graph through `recap.agent.runtime.build_real_runtime`.
The runtime constructs one shared ledger, one explicit tool registry, and the
contract-driven Think/Check/Act/Observe loop. It has no separate Plan node.

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
