# Scenarios and fixtures

A **scenario** is one test call: who calls, what they say, and what must happen. A **fixture** is the mock data behind the call.

- Scenarios are YAML files in `scenarios_dir`, one scenario per file.
- Fixtures are JSON files in `fixtures_dir`.
- Check them with `uv run pv validate`. Unknown keys are errors.

## A complete scenario

```yaml
id: book-happy-path
title: Customer books an appointment, happy path
agent: acme-demo
language: en
risk: critical
tags: [booking, happy-path, smoke]
fixture: acme-clinic.json
caller:
  mode: scripted
  voice: af_heart
  speed: 1.0
  turns:
    - say: "Hi, I need an appointment on Tuesday at 10 AM."
    - say: "My name is Sam Rivera."
    - say: "Yes, that is correct."
    - say: "Thank you, goodbye."
audio:
  noise: none
expect:
  tool_calls:
    - name: find_slot
      args: { day: tuesday, time: "10:00" }
    - name: book_appointment
      args: { name: "Sam Rivera", day: tuesday, time: "10:00" }
  forbidden_tools: [cancel_appointment]
  order: [find_slot, book_appointment]
  final_state:
    appointments:
      - { name: "Sam Rivera", day: tuesday, time: "10:00" }
  latency_ms: { p50: 1500, p95: 2500 }
  wer_max: 0.2
  never_say: ["system prompt"]
  judge:
    - "The agent told the caller that the appointment is booked for Tuesday at 10 AM."
    - "The agent stayed polite."
limits: { max_turns: 8, max_seconds: 120, silence_timeout_s: 15 }
runs: 1
```

## Scenario reference

### Top level

| Key | Type | Default | Meaning |
|---|---|---|---|
| `id` | string, kebab-case | required | Unique id. It must equal the file name without `.yaml` |
| `title` | string | required | One line for people |
| `agent` | string | required | An agent `id` from `panchovoice.yaml` |
| `language` | `en`, `es` | `en` | Language of the call. It is sent to the agent and to the judge |
| `risk` | `critical`, `normal` | `normal` | A `critical` scenario must pass every run, or the gate fails |
| `tags` | list of strings | `[]` | Free labels, used by `pv run --tag` |
| `fixture` | file name | none | A file in `fixtures_dir` (`acme-clinic.json` or `acme-clinic`). Needed when you use `expect.tool_calls` or `expect.final_state` |
| `caller` | object | required | Who calls. See below |
| `audio` | object | no noise | Degradations of the caller audio |
| `expect` | object | no checks | What must happen |
| `limits` | object | see below | Safety limits of the call |
| `runs` | integer, 1 to 100 | `1` | How many times to repeat this scenario in a suite |

### `caller`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `caller.mode` | `scripted`, `ai` | `scripted` | Fixed script, or an LLM that plays the customer |
| `caller.voice` | string | `af_heart` | Kokoro voice id |
| `caller.speed` | number, above 0.25 up to 3.0 | `1.0` | Speech speed |
| `caller.turns` | list | none | Scripted mode: the lines to say. Required for `scripted` |
| `caller.turns[].say` | string | required | The text of the turn |
| `caller.turns[].interrupt_after_ms` | integer | none | Barge-in: start this turn this many ms after the agent starts its reply, while the agent is still talking |
| `caller.turns[].pause_ms` | integer, up to 120000 | none | Extra silence before the caller says this turn |
| `caller.persona` | string | none | AI mode: who the caller is. Required for `ai` |
| `caller.goal` | string | none | AI mode: what the caller wants. Required for `ai` |
| `caller.facts` | object | `{}` | AI mode: the only facts the caller knows. The AI caller must not invent other data |
| `caller.opening_line` | string | none | AI mode: the first line, said as written |
| `caller.traits.interrupts` | boolean | `false` | Accepted by the file format. The AI caller does not act on it yet (planned) |
| `caller.traits.patience_s` | number | `6` | AI mode: about how many seconds the persona waits before it gets impatient. It is given to the LLM as part of the role |

How turns work in scripted mode: each turn waits for the agent to finish speaking, then the caller says the text. After the last turn the caller hangs up. Turns after `limits.max_turns` are not said.

How the AI caller works: after each agent reply, the LLM writes one short sentence for the customer. It ends the call when the goal is done or is clearly not possible, or at `limits.max_turns`.

### `audio`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `audio.noise` | `none`, `cafe`, `street`, `white` | `none` | Noise mixed into the caller speech |
| `audio.snr_db` | number | `20` | Signal to noise ratio in dB. Lower is noisier. 10 is a hard line |

The noise is generated, not recorded (white noise, a street-like rumble with passing bursts, a cafe-like babble). It is mixed into the caller lines only. The silence between lines stays clean.

### `expect`

Every key is optional. A key that is not there makes no check.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `expect.tool_calls` | list of `{name, args}` | `[]` | Every listed call must happen. `args` is a subset match: the listed args must be in the real call with equal values |
| `expect.forbidden_tools` | list of names | `[]` | These tools must never be called |
| `expect.no_extra_tools` | boolean | `false` | When true, any tool call that is not in `tool_calls` fails |
| `expect.order` | list of names | `[]` | These tools must be called in this relative order (other calls may come between) |
| `expect.final_state` | object | none | Collection name to a list of rows. Each row must be in the mock data after the call (subset match). An empty list means the collection must be empty |
| `expect.latency_ms.p50` | integer, ms | none | Maximum median voice to voice latency |
| `expect.latency_ms.p95` | integer, ms | none | Maximum P95 voice to voice latency |
| `expect.barge_in.max_stop_ms` | integer, ms | none | Maximum time for the agent to stop after the caller interrupts. Checked only when a turn has `interrupt_after_ms` |
| `expect.wer_max` | number | none | Maximum word error rate (0.15 means 15 percent). Needs the agent to send what it heard |
| `expect.must_say` | list of strings | `[]` | The agent must say each of these at least once (substring, case-insensitive) |
| `expect.never_say` | list of strings | `[]` | The agent must never say any of these (substring, case-insensitive) |
| `expect.judge` | list of strings | `[]` | Statements for the LLM judge. Each one gets a yes or no with a quote |

Argument values are compared with the same normalization as the mock tools (see "Normalization" below). So `time: "10:00"` matches a call with `"10 am"`.

Two checks run on every scenario, with nothing to write:

| Check | Fails when |
|---|---|
| `agent_replied` | The agent never spoke |
| `call_completed` | The agent closed the connection without an `end` message |

### `limits`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `limits.max_turns` | integer, 1 to 200 | `12` | Maximum caller turns |
| `limits.max_seconds` | integer, 5 to 3600 | `180` | Maximum call length |
| `limits.silence_timeout_s` | number, up to 300 | `15` | How long the caller waits for an agent reply before it moves on |

## Examples

### Scripted caller

See the complete scenario at the top. Use a script when you need the same words every run: exact tool arguments, WER, barge-in.

### AI caller

```yaml
id: ai-caller-booking
title: AI caller books an appointment in a free conversation
agent: acme-demo
language: en
risk: normal
tags: [booking, ai-caller]
fixture: acme-clinic.json
caller:
  mode: ai
  voice: af_heart
  persona: "Sam Rivera, a busy customer of ACME clinic. Short answers. Gets impatient with long replies."
  goal: "Book an appointment for Tuesday at 3:30 PM, confirm it if asked, then say goodbye."
  facts: { name: "Sam Rivera", phone: "555-0142", wanted_day: "Tuesday", wanted_time: "3:30 PM" }
  opening_line: "Hi, I need an appointment."
  traits: { interrupts: false, patience_s: 6 }
expect:
  tool_calls:
    - name: book_appointment
      args: { name: "Sam Rivera", day: tuesday, time: "15:30" }
  final_state:
    appointments:
      - { name: "Sam Rivera", day: tuesday, time: "15:30" }
  judge:
    - "The appointment was booked for Tuesday at 3:30 PM for Sam Rivera."
limits: { max_turns: 8, max_seconds: 150, silence_timeout_s: 15 }
```

The words of an AI caller change from run to run. Check facts (tool calls, final state), not exact phrases.

### Barge-in

```yaml
caller:
  mode: scripted
  voice: am_michael
  speed: 1.1
  turns:
    - say: "I need an appointment on Tuesday at 11 AM."
    - say: "Sorry, just book it. My name is Sam Rivera."
      interrupt_after_ms: 1200
    - say: "Yes. Thanks, bye."
expect:
  barge_in: { max_stop_ms: 1500 }
```

The second turn starts 1.2 seconds after the agent begins its reply. The report shows how long the agent kept talking. If the agent was already silent at that moment, the check is `error` ("barge-in not exercised"), not `failed`.

### Noise

```yaml
audio:
  noise: street
  snr_db: 10
expect:
  wer_max: 0.35
```

### Silence in the middle of a call

```yaml
turns:
  - say: "Hi, I'd like to check my balance."
  - say: "Sorry, I was looking for my card. My name is Jordan Lee."
    pause_ms: 6000
```

### Spanish

```yaml
id: slot-not-available
title: Cliente pide el viernes, que no tiene horas libres (español)
agent: acme-demo
language: es
fixture: acme-clinic.json
caller:
  mode: scripted
  voice: ef_dora
  turns:
    - say: "Hola, necesito una cita el viernes a las diez."
    - say: "Me llamo Lucia Moreno."
    - say: "Bueno, no importa. Gracias, adiós."
expect:
  forbidden_tools: [cancel_appointment]
  final_state:
    appointments: []
  judge:
    - "The agent told the caller that Friday at ten is not available and did not claim to have booked it."
```

Use a Spanish Kokoro voice (the demo uses `ef_dora` and `em_alex`). Judge statements can stay in English. Day names in Spanish (`martes`) and English (`tuesday`) match each other in tool arguments.

## Fixture reference

The fixture is the one source of truth for the mock backend and for the caller facts. Put the same names and numbers in `caller.facts` or in the script that are in the fixture `state`.

```json
{
  "id": "acme-clinic",
  "description": "ACME clinic (fictional) with open slots and customers",
  "tools": [
    {"name": "find_slot", "description": "Check if a slot is free on a day and time",
     "params": {"day": "string", "time": "string"},
     "action": "find", "collection": "slots", "match": ["day", "time"], "returns": "first_or_error"},
    {"name": "list_slots", "description": "List the free slots of a day",
     "params": {"day": "string"},
     "action": "find", "collection": "slots", "match": ["day"], "returns": "all"},
    {"name": "book_appointment", "description": "Book a slot for a customer",
     "params": {"name": "string", "day": "string", "time": "string"},
     "action": "insert", "collection": "appointments",
     "requires": {"collection": "slots", "match": ["day", "time"]}},
    {"name": "check_balance", "description": "Get a customer balance",
     "params": {"name": "string"},
     "action": "find", "collection": "customers", "match": ["name"],
     "returns": "first_or_error", "fields": ["name", "balance"]},
    {"name": "cancel_appointment", "description": "Cancel an appointment",
     "params": {"name": "string", "day": "string"},
     "action": "delete", "collection": "appointments", "match": ["name", "day"]}
  ],
  "state": {
    "slots": [{"day": "tuesday", "time": "10:00"}, {"day": "tuesday", "time": "11:00"}],
    "customers": [{"name": "Sam Rivera", "phone": "555-0142", "balance": 120.5}],
    "appointments": []
  }
}
```

### Fixture keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `id` | string, lowercase with hyphens | required | Must equal the file name without `.json` |
| `description` | string | empty | One line for people |
| `tools` | list | `[]` | The mock tools the agent can call |
| `state` | object | `{}` | Collections: a name to a list of rows. Each call starts from a fresh copy |

### Tool keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | string (letters, digits, underscore) | required | Tool name. Names must be unique |
| `description` | string | empty | Shown to the agent in the tool list |
| `params` | object: name to `string`, `number`, `integer` or `boolean` | `{}` | The parameters of the tool |
| `action` | `find`, `insert`, `delete` | required | What the tool does |
| `collection` | string | required | The collection in `state` that the tool works on |
| `match` | list of param names | `[]` | The params used to find rows. Required for `find` and `delete` |
| `returns` | `first_or_error`, `all` | `first_or_error` | For `find`: the first matching row, or all matching rows |
| `fields` | list of names | all fields | For `find`: return only these fields of a row |
| `requires` | `{collection, match}` | none | For `insert`: a row must exist in this other collection, or the call fails |

### Actions

| Action | What happens | Response |
|---|---|---|
| `find` | Looks for rows where every `match` param equals the row value | `{"ok": true, "result": <row, or list of rows for "all">}` or `{"ok": false, "error": "not_found"}` |
| `insert` | Appends the params as a new row. With `requires`: fails when no row matches. When `requires` points at `slots`, the matched slot is removed (the slot is taken) | `{"ok": true}` or `{"ok": false, "error": "slot_not_available"}` |
| `delete` | Removes the matching rows | `{"ok": true, "deleted": n}` |

Other error responses: `{"ok": false, "error": "missing_params", "params": [...]}` when a needed param is empty, and `{"ok": false, "error": "unknown_tool"}`. The HTTP status is always 200; errors are in the body.

Every call is recorded as `{t_ms, name, args, response}`, also the failed ones.

### Normalization

Agents say the same thing in many ways. The mock tools and the checks normalize values before they compare:

| Value | Rule | Example |
|---|---|---|
| Strings | Case-insensitive, extra spaces removed | `"sam  rivera"` equals `"Sam Rivera"` |
| A param named `time` | Converted to 24 hour `HH:MM` | `"10"`, `"10:00"`, `"10 am"`, `10` become `"10:00"`; `"2 pm"` becomes `"14:00"` |
| A param named `day` | Lowercase English weekday; short forms and Spanish names are accepted | `"Tue"`, `"martes"` become `"tuesday"` |
| Numbers | A number and the same number as a string are equal | `120.5` equals `"120.5"` |

The rules for time and day apply only to params with exactly those names. Name your params `day` and `time` to get them.

## The 15 demo scenarios

All use the fixture `acme-clinic.json` and the agent `acme-demo`.

| Id | What it tests | Risk |
|---|---|---|
| `book-happy-path` | The full booking flow: `find_slot` then `book_appointment` with exact args, final state, WER, polite | critical |
| `confirm-before-booking` | The agent reads the day and time back and gets a yes before it books. This is the check that the planted bug fails | critical |
| `book-happy-path-ai` | The same booking as `book-happy-path`, with an AI caller that answers the agent (it repeats and spells its name if the agent asks again) | critical |
| `ai-caller-booking` | An AI caller books in a free conversation | normal |
| `impatient-interrupts` | Barge-in: the caller talks over the agent; stop time and context after the interruption | normal |
| `heavy-accent-voice` | A British voice books a slot; WER with an accent | normal |
| `mispronounced-name` | The name is said in an odd way and then spelled; the agent must check it and not invent a balance | normal |
| `noisy-line` | Booking from a noisy street at SNR 10 dB; WER with noise | normal |
| `long-silence` | The caller goes quiet for 6 seconds; the agent must not end the call | normal |
| `change-of-mind` | Spanish. The caller changes the time before booking; the agent must book the new time | normal |
| `slot-not-available` | Spanish. The caller asks for a day with no free slots; nothing may be booked | normal |
| `out-of-scope-request` | The caller asks for medical advice; the agent must refuse and not invent | normal |
| `unknown-customer-balance` | Balance for a customer that does not exist; the agent must not invent an amount | critical |
| `jailbreak-prompt-leak` | The caller tries to make the agent read its instructions and tool names | critical |
| `data-leak-other-customer` | The caller asks for the balance of another customer | critical |

The demo scenarios use loose latency limits (`p50: 6000, p95: 8000`) because the demo agent runs on a CPU. For a real agent, start near `p50: 1500, p95: 2500` and then tighten against your own baseline.

## Tips for good scenarios

- **Check facts first.** Tool calls and final state are exact. The judge is for conduct that facts cannot show.
- **One purpose per scenario.** A scenario that tests booking, noise and a jailbreak at once is hard to read when it fails.
- **Write judge statements that are true or false.** "The agent read the day and the time back before booking" is good. "The agent was helpful" is vague.
- **Say when, not only what.** The judge sees the tool calls on the timeline. "Before the book_appointment tool call, the agent asked the caller to confirm" can be checked.
- **Keep the data in the fixture.** The caller facts and the mock data must agree. Never let the caller invent a name or a number.
- **Use `forbidden_tools` and an empty `final_state` list** for "must not do" tests.
- **Use `never_say` for leaks**: parts of the system prompt, tool names, another customer's data.
- **Mark `critical` only what must never break**: money, privacy, security, the main flow.
- **Use `runs: 3` or more** for scenarios that depend on timing (barge-in) or on an LLM (AI caller). The gate counts runs, so one unlucky run does not hide.
- **Start with scripted callers.** Add AI callers later, for variety.
- **Set latency limits from your own baseline**, not from a number in a blog post. See [metrics.md](metrics.md).
- **Let Panchito write the first draft**, then review it. See [panchito.md](panchito.md).
