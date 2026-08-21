# ReCAP architecture

ReCAP is a behavior-layer security mechanism for ReAct agents. It does not
modify model parameters and assumes registered tools implement their declared
specifications. There is no separate Plan node or task-plan state machine.
Planning remains part of each ReAct Think stage.

## Contract-driven loop

```text
START
  -> Think: submit one public intent certificate
  -> Think-to-Act: verify commitment, authority, policy and argument bounds
  -> Act: execute one approved call through the trusted tool wrapper
  -> Observe: bind the result to call_id, tool and actual parameters
  -> Act-to-Observe: verify effects, receipts and evidence obligations
  -> Observe-to-Think: label sources and isolate untrusted instructions
       |-- continue or deterministic replan -> Think
       |-- block or human approval -> END
       `-- final answer from a later Think only when no obligation is pending
```

## Public intent certificate

Every tool-producing Think round emits only externally verifiable commitments:

- current subgoal;
- proposed tool and exact argument constraints;
- traceable authority derived from trusted task context;
- allowed, forbidden and required observable effects;
- required receipts and evidence.

Runtime code combines the certificate with the original task, registered tool
capabilities, effective permissions, policy references and an always-on
normative baseline. The baseline is fail-closed, forbids authority expansion
and unproven success, and preserves pending obligations.

## Shared ledger and contracts

The append-only hash-chained ledger records contract versions, authority and
policy references, proposed and actual calls, source labels, violations,
purification decisions and evidence obligations. Each round has one runtime
contract, while unresolved obligations remain visible in graph state across
rounds.

## Transition checks

Think-to-Act compares the actual candidate tool and arguments with the public
certificate, task allowlist and runtime contract before side effects occur.

Act-to-Observe binds the trusted result to the action's `call_id`, tool name and
actual arguments. Complete evidence advances to isolation. Missing evidence
transitions the contract to `evidence_pending`, records a Pending obligation and
forbids a success claim.

Observe-to-Think treats low-trust external content as data, removes control-like
instructions, records a replayable violation witness and prevents goal or
authority changes caused by the observation.

## Deterministic recovery

Recovery is selected from the violation type rather than freely chosen by the
LLM: block, parameter contraction, observation purification, replan by returning
to Think, or human approval. A witness contains stable rule identifiers,
expected contract terms, actual trajectory facts and the selected recovery.

## Source ownership

```text
src/recap/
|-- agent/          # Graph composition, runtime and cross-round state
|-- contracts/      # Per-round semantic contract lifecycle
|-- ledger/         # Append-only hash-chained events
|-- nodes/          # Think, three transition checks, Act and Observe
|-- security/       # Approved-action binding
|-- tools/          # Explicit registry and trusted execution wrapper
|-- verification/   # Deterministic argument checks
|-- integration.py  # Contract and ledger transitions
`-- schemas.py      # Certificates, events, witnesses and obligations
```
