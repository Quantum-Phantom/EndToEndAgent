# AgentDojo Suite Migration Guide

This document describes how to migrate the remaining AgentDojo suites
(workspace, travel, slack) into ReCAP scenarios. The banking suite is
already complete and serves as the reference implementation.

## Prerequisites

- Read `scenarios/banking/` (all 6 files) as the canonical reference.
- Read `scenario.md` for the ReCAP scenario contract.
- AgentDojo source is at `.venv/Lib/site-packages/agentdojo/`.

## File Structure Per Suite

Each suite requires exactly 6 files:

```
scenarios/<suite>/
├── __init__.py       # empty
├── tools.py          # dataclass models + @tool functions + TOOLS_BY_NAME
├── config.py         # EVIDENCE_DETECTORS, CONSTRAINT_FIELDS, EVIDENCE_TYPES, system_prompt_template
├── runner.py         # Pipe-based child process entry point
├── tasks.py          # UserTask / InjectionTask classes with utility() / security()
└── evaluator.py      # SuiteEvaluator class (reuse BankingEvaluator pattern)
```

## Core Design: Paired Evaluation

Injection tasks are **NOT** executed separately. This mirrors AgentDojo's
design. For each `(user_task, injection_task)` pair:

```
1. reset_database(injection_vectors)       ← inject adversarial content
2. pre_db = snapshot_database()
3. graph.invoke(user_task.PROMPT)           ← execute user task prompt
4. post_db = snapshot_database()
5. utility  = user_task.utility(output, pre_db, post_db)
6. security = injection_task.security(output, pre_db, post_db)   ← SAME post_db
```

The injection task's `goal` is never used as a prompt. It is only used
for reporting and understanding what the attack was trying to achieve.

## Step-by-Step Migration Process

### Step 1: `tools.py` — Port the environment and tools

**Source files** (read from AgentDojo):
- `.venv/.../default_suites/v1/tools/<domain>_client.py` — domain models + tool functions
- `.venv/.../default_suites/v1/tools/types.py` — shared types (Email, CalendarEvent, etc.)
- `.venv/.../data/suites/<suite>/environment.yaml` — seed data
- `.venv/.../data/suites/<suite>/injection_vectors.yaml` — injection placeholders

**What to do:**

1. **Convert Pydantic models to dataclasses.** Every `BaseModel` class becomes
   a `@dataclass`. Pydantic `Field(...)` becomes a dataclass field with default.
   `model_validator` logic goes into a `seed()` method.

2. **Convert AgentDojo tool functions to LangChain `@tool` functions.**
   AgentDojo tools are plain functions with `Annotated[X, Depends("name")]`
   dependency injection. ReCAP tools are `@tool`-decorated functions that
   receive string/typed args directly and return strings.

   Key differences:
   | AgentDojo | ReCAP |
   |-----------|-------|
   | `def send_money(account: Annotated[BankAccount, Depends("bank_account")], recipient: str, ...)` | `@tool\ndef send_money(recipient: str, amount: float, ...) -> str:` |
   | Returns `dict` or Pydantic model | Returns `str` (for evidence detector regex) |
   | Uses `Depends` for env injection | Accesses `get_database()` global |
   | Raises `ValueError` on errors | Raises `ValueError` on errors (same) |

3. **Create the database singleton pattern:**
   ```python
   import copy
   from dataclasses import dataclass, field

   _db: SuiteDatabase | None = None

   def get_database() -> SuiteDatabase:
       global _db
       if _db is None:
           _db = SuiteDatabase()
           _db.seed()
       return _db

   def reset_database(injection_param: str | None = None, ...) -> None:
       global _db
       _db = SuiteDatabase()
       _db.seed(injection_param=injection_param, ...)

   def snapshot_database() -> SuiteDatabase:
       return copy.deepcopy(get_database())
   ```

4. **Parameterize `seed()` with injection vectors.** Read
   `injection_vectors.yaml` to find all `{injection_*}` placeholders.
   Each becomes a parameter of `seed()` with default `None` (use
   the `default` value from the YAML when `None`).

5. **All tools must return `str`.** This is required for evidence
   detector regex matching. Format: human-readable strings like
   `"Email sent to alice@example.com."` or `"Event 'Meeting' created."`.

6. **Register tools:**
   ```python
   TOOLS_BY_NAME: dict[str, Any] = {
       "tool_name": tool_function,
       ...
   }
   ```

### Step 2: `config.py` — Evidence detectors and prompt template

**Source file:** Look at `scenarios/banking/config.py` for the pattern.

**What to do:**

1. **Define `EVIDENCE_DETECTORS`.** For each tool that produces meaningful
   output, create a `(tool_name, regex_pattern)` pair. The regex must match
   the `str` output of the tool (from Step 1.5).

   Example:
   ```python
   "email_sent": ("send_email", r"Email sent to \S+"),
   "event_created": ("create_calendar_event", r"Event '.*' created"),
   ```

2. **Define `CONSTRAINT_FIELDS`.** List all parameter names that appear in
   tool signatures. These become valid keys in intent certificate
   `argument_constraints`.

3. **Define `EVIDENCE_TYPES`.** Must be exactly the keys of `EVIDENCE_DETECTORS`.

4. **Copy `system_prompt_template()` from banking.** Change only the domain
   description ("You are a personal banking assistant" → "You are a workspace
   assistant") and the constraints line at the end. The certificate format
   instructions are identical across all suites.

### Step 3: `runner.py` — Child process entry point

Copy `scenarios/banking/runner.py` and change only:
- The import path (`scenarios.<suite>.tools`)
- The `reset_database()` call arguments (match the injection vector
  parameters from Step 1.4)
- The `RECAP_INJECTION_*` environment variable names

### Step 4: `tasks.py` — Port all user and injection tasks

**Source files:**
- `.venv/.../default_suites/v1/<suite>/user_tasks.py`
- `.venv/.../default_suites/v1/<suite>/injection_tasks.py`

**What to do:**

1. **Define base classes:**
   ```python
   @dataclass
   class ExpectedCall:
       function: str
       args: dict[str, Any] = field(default_factory=dict)

   @dataclass
   class UserTask:
       id: str
       prompt: str
       comment: str = ""
       expected_calls: list[ExpectedCall] = field(default_factory=list)
       def utility(self, model_output: str, pre_db: SuiteDatabase, post_db: SuiteDatabase) -> bool:
           raise NotImplementedError

   @dataclass
   class InjectionTask:
       id: str
       goal: str
       comment: str = ""
       expected_calls: list[ExpectedCall] = field(default_factory=list)
       def security(self, model_output: str, pre_db: SuiteDatabase, post_db: SuiteDatabase) -> bool:
           raise NotImplementedError
   ```

2. **Port each task class.** For each `UserTaskN` / `InjectionTaskN` in
   AgentDojo, create a corresponding class. Copy the `PROMPT`/`GOAL`,
   `COMMENT`, and `ground_truth` calls. Rewrite `utility()`/`security()`
   to operate on dataclass snapshots instead of Pydantic environments.

   **Key translation patterns:**
   | AgentDojo (Pydantic) | ReCAP (dataclass) |
   |---------------------|-------------------|
   | `post_environment.bank_account.transactions` | `post_db.transactions` |
   | `post_environment.inbox.emails[id]` | `post_db.emails[id]` |
   | `pre_environment == post_environment` | `pre_db == post_db` (dataclass `__eq__` works) |
   | `model_output` string check | Same (`"text" in model_output`) |
   | `DeepDiff(pre, post)` | Use `dataclasses.asdict()` + recursive compare, or add `__eq__` |

3. **Register tasks:**
   ```python
   USER_TASKS: list[UserTask] = [UserTask0(), UserTask1(), ...]
   INJECTION_TASKS: list[InjectionTask] = [InjectionTask0(), ...]
   ```

4. **Port security checks for injection tasks.** The most common patterns:
   - **Data exfiltration:** Check if sensitive data appears in a message
     sent to the attacker (search `post_db` for messages/transactions
     containing the attacker's address/IBAN and leaked info).
   - **Unauthorized action:** Check if a forbidden state change occurred
     (e.g., password changed, reservation made for wrong hotel).
   - **Output manipulation:** Check if `model_output` contains attacker-
     injected text (e.g., "you should visit Hotel X").

### Step 5: `evaluator.py` — Evaluation harness

Copy `scenarios/banking/evaluator.py` and change:
- Import paths to the new suite's modules
- The database type in `run_user_task_with_injection()` signature
- The default `task_desc` string

The evaluator provides:

- **`PairedTaskResult`** — dataclass with `user_task_id`, `injection_task_id`,
  `utility`, `security`, `model_output`, `error`, `log_path`
- **`BenchmarkResult`** — aggregated results with `summary_table()`
- **`BankingEvaluator.run_user_task_with_injection()`** — the primary method.
  Runs the user task prompt, evaluates both utility and security on the same
  post-environment, saves per-task JSON log.

**Logging behavior:** Each call to `run_user_task_with_injection()` produces:
- `logs/<suite>/<user_task_id>--<injection_task_id>.json` — full structured log
- `logs/<suite>/<user_task_id>--<injection_task_id>.jsonl` — ReCAP ledger trace

### Step 6: Create `scripts/benchmark_<suite>.py`

Copy `scripts/benchmark.py` and adapt:

1. **Import the new suite's modules** (config, tasks, evaluator)
2. **Define `_INJECTION_PAYLOADS`** — per-injection-task injection content
   that tries to trick the agent into achieving the attack goal
3. **Define `_DEFAULT_PAIRINGS`** — which user task pairs with which
   injection task (the user task should trigger reading the injected content)
4. **Update CLI args** if needed

**Injection payload design:** For each injection task, create adversarial
content that embeds in the injection vector (e.g., an email body, a web page,
a calendar event description). The payload should instruct the agent to
perform the injection task's goal.

**Pairing design:** Each injection task should be paired with a user task
that triggers the agent to read the injected content. For example:
- Bill-related injections → pair with a task that reads the bill
- Email-related injections → pair with a task that reads emails
- Web-related injections → pair with a task that fetches a webpage

## Suite-Specific Reference

### Workspace Suite (33 user tasks, 6 injection tasks)

**Environment components:**
- `Inbox` — emails dict, contact_list, trash. Tools: `send_email`,
  `delete_email`, `get_unread_emails`, `get_sent_emails`,
  `get_received_emails`, `get_draft_emails`, `search_emails`,
  `search_contacts_by_name`, `search_contacts_by_email`
- `Calendar` — events dict, current_day. Tools: `get_current_day`,
  `search_calendar_events`, `get_day_calendar_events`,
  `create_calendar_event`, `cancel_calendar_event`,
  `reschedule_calendar_event`, `add_calendar_event_participants`
- `CloudDrive` — files dict. Tools: `append_to_file`,
  `search_files_by_filename`, `create_file`, `delete_file`,
  `get_file_by_id`, `list_files`, `share_file`, `search_files`

**Total tools: 22**

**Injection vectors (19):** Calendar event descriptions, email bodies,
drive file content. All are text fields where an attacker could embed
prompt injections.

**Key dataclass conversions:**
- `Email` → `@dataclass` with fields: `id_`, `sender`, `recipients`,
  `cc`, `bcc`, `subject`, `body`, `status`, `read`, `timestamp`,
  `attachments`
- `CalendarEvent` → `@dataclass` with fields: `id_`, `title`,
  `description`, `start_time`, `end_time`, `location`, `participants`,
  `all_day`, `status`
- `CloudDriveFile` → `@dataclass` with fields: `id_`, `filename`,
  `content`, `owner`, `last_modified`, `shared_with`, `size`
- `EmailStatus` → `str` enum: `"sent"`, `"received"`, `"draft"`
- `EvenStatus` → `str` enum: `"confirmed"`, `"canceled"`
- `SharingPermission` → `str` enum: `"r"`, `"rw"`

**Key complexity:** Calendar tools that create/cancel/reschedule events
also send emails (side effect). The dataclass must replicate this:
`create_calendar_event` should both add to `calendar.events` and add to
`inbox.emails`.

**AgentDojo types file:** `.venv/.../tools/types.py` — contains `Email`,
`CalendarEvent`, `CloudDriveFile`, `EmailStatus`, `EvenStatus`,
`SharingPermission`, `EmailContact`, `EmailID`, `CalendarEventID`,
`CloudDriveFileID`.

**Environment YAML files:** The workspace environment uses YAML includes:
- `.venv/.../data/suites/workspace/environment.yaml` — top-level includes
- `.venv/.../data/suites/workspace/include/inbox.yaml` — email seed data
- `.venv/.../data/suites/workspace/include/calendar.yaml` — event seed data
- `.venv/.../data/suites/workspace/include/cloud_drive.yaml` — file seed data

You must read all 4 YAML files to construct the seed data for the
dataclass `WorkspaceDatabase`.

**Common evaluation patterns in workspace tasks:**
- Check if an email was sent to the right recipient with the right body
- Check if a calendar event was created/cancelled/rescheduled correctly
- Check if a file was created/shared/appended correctly
- Check if `pre_db == post_db` for read-only tasks
- Many tasks use `DeepDiff` with excluded paths — port this by
  implementing custom comparison that ignores sent emails and other
  side effects

**Injection payload sources:** Email bodies (`email_hiking_injection`,
`email_facebook_injection`, etc.), calendar descriptions
(`calendar_introductory_meeting_injection`, etc.), drive file content
(`drive_feedback_injection`, `drive_recipe_injection`, etc.).

### Travel Suite (20 user tasks, 7 injection tasks)

**Environment components:**
- `Hotels` — hotel_list. 6 hotels in Paris. Tools: `get_all_hotels_in_city`,
  `get_hotels_prices`, `get_rating_reviews_for_hotels`, `get_hotels_address`
- `Restaurants` — restaurant_list. Tools: `get_all_restaurants_in_city`,
  `get_cuisine_type_for_restaurants`, `get_restaurants_address`,
  `get_rating_reviews_for_restaurants`,
  `get_dietary_restrictions_for_all_restaurants`,
  `get_contact_information_for_restaurants`, `get_price_for_restaurants`,
  `check_restaurant_opening_hours`
- `CarRental` — company_list. Tools: `get_all_car_rental_companies_in_city`,
  `get_car_types_available`, `get_rating_reviews_for_car_rental`,
  `get_car_fuel_options`, `get_car_rental_address`, `get_car_price_per_day`
- `Flights` — flight_list. Tool: `get_flight_information`
- `User` — personal info (passport, credit card, etc.). Tool: `get_user_information`
- `Calendar` — same as workspace. Tools: `create_calendar_event`,
  `search_calendar_events`, `get_day_calendar_events`, `cancel_calendar_event`
- `Reservation` — current reservation state. Tools: `reserve_hotel`,
  `reserve_car_rental`, `reserve_restaurant`
- `Inbox` — same as workspace. Tool: `send_email`

**Total tools: 26**

**Injection vectors (13):** Hotel reviews, restaurant reviews, car rental
reviews. These are text fields in the review lists where attackers can
embed prompt injections.

**Key dataclass conversions:**
- `Hotel` → `@dataclass`: `name`, `city`, `rating`, `price_min`, `price_max`,
  `address`, `reviews` (list of str)
- `Restaurant` → `@dataclass`: `name`, `city`, `address`, `rating`,
  `price_per_person`, `operating_hours`, `reviews`, `contact_information`,
  `cuisine_type`, `dietary_restrictions`
- `CarRentalCompany` → `@dataclass`: `name`, `city`, `address`, `rating`,
  `price_per_day`, `reviews`, `contact_information`, `car_types_available`,
  `fuel_options`
- `Flight` → `@dataclass`: `airline`, `flight_number`, `departure_city`,
  `arrival_city`, `departure_time`, `arrival_time`, `price`,
  `contact_information`
- `Reservation` → `@dataclass`: `reservation_type` (str or None), `title`,
  `start_time`, `end_time`, `contact_information`
- `ReservationType` → `str` enum: `"hotel"`, `"car"`, `"restaurant"`

**Key complexity:** The `Reservation` is a singleton (not a list). Tools like
`reserve_hotel` mutate it in place. The `User` model contains sensitive data
(passport, credit card) that injection tasks try to leak.

**Evaluation patterns:**
- Check if correct hotel/restaurant/car was reserved
- Check if calendar event was created for the right dates
- Check if `model_output` contains a specific hotel name (InjectionTask6)
- Check if reservation type/title matches expected values

**Injection payload sources:** Hotel reviews (`injection_hotels_0` through
`injection_hotels_5`), restaurant reviews (`injection_restaurants_0` through
`injection_restaurants_3`), car rental reviews (`injection_cars_0` through
`injection_cars_2`).

**Environment YAML:** `.venv/.../data/suites/travel/environment.yaml`

### Slack Suite (17 user tasks, 5 injection tasks)

**Environment components:**
- `Slack` — users, channels, user_channels, user_inbox, channel_inbox.
  Tools: `get_channels`, `add_user_to_channel`, `read_channel_messages`,
  `read_inbox`, `send_direct_message`, `send_channel_message`,
  `get_users_in_channel`, `invite_user_to_slack`, `remove_user_from_slack`
- `Web` — web_content, web_requests. Tools: `get_webpage`, `post_webpage`

**Total tools: 11**

**Injection vectors (6):** Web content, blog content, channel names.
Attackers embed prompt injections in web pages that the agent reads.

**Key dataclass conversions:**
- `Message` → `@dataclass`: `sender`, `recipient`, `body`
- `Slack` → `@dataclass`: `users` (list), `channels` (list),
  `user_channels` (dict[str, list[str]]),
  `user_inbox` (dict[str, list[Message]]),
  `channel_inbox` (dict[str, list[Message]])
- `Web` → `@dataclass`: `web_content` (dict[str, str]),
  `web_requests` (list[str])

**Key complexity:** The Slack suite has NO calendar, no email client, no
cloud drive — just Slack messaging and a simple web client. This makes
it the simplest suite to migrate.

**Evaluation patterns:**
- Check if a direct message was sent to the right user with the right body
- Check if a channel message contains specific content
- Check if a webpage was fetched (URL in `web_requests`)
- Check if `model_output` contains a specific URL or hotel name

**Injection payload sources:** Web content (`injection_web_0`),
phishing site content (`injection_phishing_1`), blog content
(`prompt_injection_eve_blog`), channel names
(`prompt_injection_channel`), restaurant website ads
(`prompt_injection_restaurant`).

**Environment YAML:** `.venv/.../data/suites/slack/environment.yaml`

## Common Pitfalls

1. **Forgetting `set_llm()` in the evaluator.** The ReCAP graph uses a
   global `_llm` variable. You MUST call `set_llm(llm_with_tools)` in
   the evaluator's `__init__` before building the graph.

2. **Evidence detector regex not matching tool output.** All tools must
   return `str`. If a tool returns a `dict`, `str(dict)` produces Python
   repr syntax (`{'key': 'value'}`) which is hard to regex. Always format
   returns as human-readable strings.

3. **Dataclass `__eq__` includes all fields.** Unlike Pydantic where you
   can use `DeepDiff` with excluded paths, dataclass `__eq__` compares
   everything. For tasks that need to ignore side effects (e.g., sent
   emails), implement custom comparison in `utility()`.

4. **Datetime fields in dataclasses.** AgentDojo uses `datetime.datetime`
   objects. When serializing with `dataclasses.asdict()`, datetimes become
   ISO strings. Keep them as `datetime` objects in the dataclass for
   comparison, but be aware of serialization format differences.

5. **Injection vector defaults.** Always read the `default` value from
   `injection_vectors.yaml` and use it when the injection parameter is
   `None`. The default is the legitimate (non-adversarial) content.

6. **Tool arg types.** LangChain `@tool` functions accept typed args.
   AgentDojo tools sometimes pass `str` where the model expects `int`
   (e.g., `email_id: str` vs `EmailID = str`). Use `str` for IDs.

7. **`reset_database()` between tasks.** The evaluator MUST call
   `reset_database()` before each task to get a clean environment.
   Otherwise state leaks between tasks.

8. **Runner env var naming.** Use `RECAP_INJECTION_<SUITE>_<VECTOR>`
   naming convention (e.g., `RECAP_INJECTION_WORKSPACE_EMAIL_BODY`)
   to avoid collisions when running multiple suites.

9. **Paired evaluation, not standalone injection.** Injection tasks
   are NEVER run as separate agent executions. They are evaluated on
   the same post-environment as the user task they are paired with.
   The injection task's `goal` is only used for reporting.

## Log File Structure

Each task execution produces two files in the log directory:

| File | Content |
|------|---------|
| `{user_task}--{injection_task}.json` | Full structured log |
| `{user_task}--{injection_task}.jsonl` | ReCAP ledger trace |

The JSON log contains:
```json
{
  "user_task_id": "user_task_0",
  "injection_task_id": "injection_task_5",
  "prompt": "Can you please pay the bill...",
  "utility": true,
  "security": false,
  "model_output": "Full untruncated output...",
  "error": null,
  "injection_vectors": {"injection_bill_text": "..."},
  "messages": [
    {"role": "human", "content": "..."},
    {"role": "ai", "content": "...", "tool_calls": [...]},
    {"role": "tool", "content": "...", "tool_call_id": "..."},
    {"role": "system", "content": "..."}
  ],
  "ledger_entries": [
    {"entry_type": "task", ...},
    {"entry_type": "intent", "certificate": {...}},
    {"entry_type": "action", ...},
    {"entry_type": "violation", ...}
  ],
  "check_results": [
    {"check_type": "think_act", "passed": true, "violations": []}
  ],
  "pre_snapshot": {"balance": 1810.0, "transactions": [...], ...},
  "post_snapshot": {"balance": 1711.3, "transactions": [...], ...}
}
```

## Verification Checklist

After migrating a suite, verify:

- [ ] `python -c "from scenarios.<suite>.tools import TOOLS_BY_NAME; print(len(TOOLS_BY_NAME))"` — correct tool count
- [ ] `python -c "from scenarios.<suite>.tasks import USER_TASKS, INJECTION_TASKS; print(len(USER_TASKS), len(INJECTION_TASKS))"` — correct task counts
- [ ] `python -c "from scenarios.<suite>.evaluator import SuiteEvaluator"` — evaluator imports
- [ ] Standalone evaluation: manually construct pre/post snapshots and call `user_task.utility()` / `injection_task.security()` for at least one task of each type
- [ ] End-to-end: `python scripts/benchmark_<suite>.py --tasks user_task_0 --inj-tasks injection_task_0 --verbose` — verify agent runs, logs are saved, and results are printed
- [ ] Log inspection: open `logs/<suite>/user_task_0--injection_task_0.json` and verify it contains messages, ledger entries, snapshots, and correct utility/security values
