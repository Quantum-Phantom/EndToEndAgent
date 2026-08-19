# Think -> Act node installation

Copy files into the project:

```text
src/recap/nodes/__init__.py
src/recap/nodes/think_act.py
tests/integration/test_think_act_node.py
```

Construct the node once with the application-level ledger service:

```python
think_act_check_node = build_think_act_check_node(ledger)
graph.add_node("think_act_check_node", think_act_check_node)
```

Replace the fixed edge from `think_act_check_node` to `act_node` with:

```python
graph.add_conditional_edges(
    "think_act_check_node",
    route_after_think_act,
    {
        "act": "act_node",
        "replan": "think_node",
        "human_approval": "human_approval_node",
        "end": END,
    },
)
```

Run:

```powershell
python -m pytest .\tests\integration\test_think_act_node.py -v -s
```

The expected result is `5 passed`. The LangGraph spy test proves that a
disallowed `multiply` call cannot enter `act_node` when the contract only
allows `add`.
