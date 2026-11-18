# Metrics

This page explains how each number is measured, why, and what its limits are.

Three rules guide every metric:

1. **The harness records both sides itself.** The same evidence exists for every agent and every platform.
2. **Facts decide.** Recorded tool calls, final data and the recording come first. The LLM judge comes second.
3. **A harness error is a third result.** It is never counted as a failure of the agent.

## The recording

Every call is recorded on one timeline that starts when the caller connects (`t0`). All times are integers in milliseconds from `t0`.

- Left channel: the caller. Right channel: the agent.
- Format: 16 kHz, 16 bit, mono per channel, saved as `stereo.wav` (plus `caller.wav` and `agent.wav`).
- The caller sends its audio in 20 ms frames at real time pace. Silence is sent as zero frames, so the stream never stops.
- The agent audio is placed on the timeline as it would be heard in real time.

## Voice to voice latency

**What it is:** the time from the moment the caller stops speaking to the moment the first agent speech is heard. This is what a person on the phone feels as "the pause".

**How it is measured:**

```
latency of a turn = agent_start_ms - caller_end_ms
```

- `caller_end_ms` is **known, not detected**. The harness made the caller audio, so it knows the exact sample where it ends. There is no guess on this side.
- `agent_start_ms` is the first speech onset on the agent channel after `caller_end_ms` and before the next caller turn. It is found with **Silero VAD** (voice activity detection) on the recording. PanchoVoiceAI uses the Silero ONNX model that ships inside faster-whisper, so there is no torch install.
- Silero reacts 30 to 90 ms late on soft sounds such as "s" or "f". The harness walks the onset backward while the audio is still above a low energy floor (at most 250 ms). This keeps the number from being biased high.

**What is reported:** per turn latency, and for the run `latency_p50_ms`, `latency_p95_ms` and `latency_max_ms`.

**Percentiles** use the nearest rank method: sort the turn latencies, take the value at rank `ceil(p / 100 * n)`. With few turns, P95 is the slowest turn. That is on purpose: with 4 turns there is no honest way to show a "95th percentile" other than the worst one.

**Why P95 matters:** the median hides slow turns. An agent with a fast median and one 6 second pause feels broken to the caller. A turn with a tool call is often the slow one, and it is often the turn that matters most.

**Limits:**

- The number includes everything between the two sides: end of turn detection, speech to text, the LLM, tools, text to speech, and the network.
- It does not split the time by stage (ears, brain, mouth). Stage latency is planned.
- If the agent never replies in a turn, that turn has no latency. If no turn has a reply, a `latency_ms` check fails.

## Barge-in stop time

**What it is:** the caller starts to talk while the agent is still talking. The stop time is how long the agent keeps talking after that.

**How it is measured:** a scripted turn with `interrupt_after_ms` starts that many ms after the agent begins its reply. On the recording:

```
stop_ms = end of the agent speech that was running when the caller started - caller_start_ms
```

`barge_in_stop_ms` of the run is the worst (longest) stop time of its interrupted turns.

**Why it matters:** an agent that talks over the caller for seconds makes the caller repeat everything. A reference point from research (the tau-Voice paper, as cited in the project plan) is to stop in under 2.0 seconds.

**Limits:**

- Barge-in is a race. If the agent had already finished when the caller started, nothing was measured. The check then has the status `error` ("barge-in not exercised"), not `failed`. Use a script and several runs.
- Today the metric is the stop time only. Two related checks are planned: does the agent keep the context after the interruption (you can ask the judge this today), and does a short "mm-hm" or a noise stop the agent by mistake.

## WER (word error rate)

**What it is:** the share of words that the agent heard wrong.

```
WER = (substitutions + deletions + insertions) / words in the reference
```

**How it is measured:**

- The **reference** is the exact text that the harness gave to the caller voice.
- The **hypothesis** is what the agent reports that it heard: the `transcript` messages with `role: user` of the `pv-ws/1` protocol.
- Both sides go through the same normalizer, then `jiwer` counts the errors over all turns of the run.

**Normalization** (same on both sides): lowercase, accents removed, punctuation removed, simple number words turned into digits in English and Spanish ("twenty one" becomes "21", "diez" becomes "10"), "a.m." and "p.m." become "am" and "pm", and "10:00" equals "10".

**Limits:**

- WER needs the agent to send what it heard. If it sends no user transcripts, `wer` is empty and a `wer_max` check has the status `error`.
- WER also contains the errors of the caller voice. A text to speech voice can say a rare name badly. Listen to the recording when a number looks odd.
- WER treats every word the same. One wrong digit in a phone number costs as much as a missing "the", but it hurts much more. **Entity accuracy** (names, dates, phone numbers, amounts) is future work. Until then, check entities with `expect.tool_calls`: the exact arguments show whether the agent got the name and the time right.
- A turn the agent did not hear at all counts as all words deleted.

## Tool calls

**How it is measured:** the agent calls its tools on the mock tool server of PanchoVoiceAI. The server records every call: time, name, exact arguments, response. The agent cannot "say" it called a tool; only real calls count.

| Check | Rule |
|---|---|
| `tool_calls` | Every expected call happened. The listed args must be in the real call with equal values (subset match, with normalization of time, day and case) |
| `forbidden_tools` | None of the listed tools was called |
| `no_extra_tools` | No call outside the expected list |
| `order` | The listed tools were called in this relative order |

A failed check lists what is missing and what was seen, for example: `1 of 2 expected calls. Missing: book_appointment(name=Sam Rivera, day=tuesday, time=10:00). Seen: find_slot(day=tuesday, time=11:00)`.

## Final state

**How it is measured:** after the call, the mock data is compared with `expect.final_state`. Each expected row must exist in its collection (subset match). An empty list means the collection must be empty.

**Why:** the transcript can say "you are booked" while nothing was booked, or the reverse. The final state is the fact. It also catches invented data: the mock values are fixed, so an agent that says another balance is wrong.

## Phrase checks

`must_say` and `never_say` are substring matches on everything the agent said, case-insensitive. A hit carries the surrounding words as evidence. They are simple and exact. Use them for leaks (prompt text, tool names) and for required words (a legal notice).

## Judge checks

**What it is:** an LLM reads the transcript, with the tool calls placed on the same timeline, and answers each statement of `expect.judge`.

**The rules:**

- **Yes or no only.** No 1 to 5 scales. A scale hides disagreement; yes or no can be checked.
- **Evidence quote.** Each answer must carry an exact quote from one transcript line or tool call line, at most 25 words. The harness checks that the quote really is in the transcript. If it is not, the check detail says so.
- **Strict.** The judge says yes only when the transcript clearly shows it.
- **One call per run.** All statements of a scenario are answered in one LLM call.
- **Judge errors are not failures.** If the brain is down, over budget, or gives no usable answer, the check has the status `error`. The run is then `error`, not `failed`.

**Limits:** a judge can be wrong. That is why facts come first, why the quote is shown next to every verdict, and why a scenario can repeat with `runs`. Calibration of the judge against human labels is planned.

## Run status

| Status | Rule |
|---|---|
| `failed` | At least one check failed |
| `error` | No check failed, but at least one could not be measured (or the call could not run) |
| `passed` | Every check passed |

## Suggested targets

Use these as a starting point only.

| Metric | Number | Where it comes from |
|---|---|---|
| Voice to voice latency, P50 | Under 800 ms is good, over 1500 ms is bad | Vendor guidance ([Hamming](https://hamming.ai), [Voice AI Space](https://www.voiceaispace.com/articles/vapi-vs-retell-vs-livekit-vs-pipecat)). Not a standard |
| Barge-in stop time | Under 2000 ms | The tau-Voice paper, as cited in the project plan |
| Gate pass rate | 95 percent | A project default. We found no source that sets this number. Other tools use 75 percent, or "all 3 runs must pass" |

The latency numbers are vendor numbers, not a norm. The useful rule is: **measure your agent today, keep that as your baseline, and do not let it get worse.** An automatic baseline comparison (block when P95 rises 10 percent or WER rises 2 points) is planned.

The demo agent is much slower than these targets (about 2 to 5 seconds on a CPU), so the demo scenarios use loose limits.

## Cost

Each result has `cost.llm_calls` and `cost.usd_estimate`. The estimate covers the brain of PanchoVoiceAI (AI caller and judge). It is 0 for `claude-cli` (subscription) and `ollama` (local). It does not include what your own agent spends on its models.
