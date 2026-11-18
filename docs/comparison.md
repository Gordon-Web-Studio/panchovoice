# Comparison with other tools

**Facts as of 2026-11-17.** Prices are as listed on each company's site on 2026-11-17. Star counts come from the GitHub API on the same day. All of this changes often, so check the linked sources before you decide.

We wrote this page to be fair. These are good tools, and some of them do things that PanchoVoiceAI does not do. Where we could not confirm a point, we say so. If something here is wrong or old, please open an issue.

## Commercial tools

| Tool | What it does well | How it connects | Price (as listed on their site on 2026-11-17) | Things to know |
|---|---|---|---|---|
| **Hamming** | Makes tests from your prompt and from real calls. Red team. Latency P50, P90, P99 | Phone, SIP, WebRTC, LiveKit, Pipecat, Vapi, Retell, ElevenLabs | Not public, through sales | No free trial. Docs are not open |
| **Coval** | The widest list of metrics. 16 languages (Spanish included), 21 noise types | Phone, SIP, WebSocket, LiveKit, Pipecat Cloud | $100, $500, or from $4,500 per month | Tool calls are counted, not validated |
| **Bluejay** | A strong CI gate (minimum score per pull request). The whole setup in one YAML file | The longest list: phone, SIP, WebSocket, LiveKit, Pipecat, Vapi, Retell, ElevenLabs | $0 plus usage, $500, or $1,000 per month | We could not confirm whether it has its own latency and WER metrics |
| **Netra** (getnetra.ai) | Low price. Has WER. MCP | LiveKit, Vapi, WebSocket | $0 or $39 per month | Not a voice specialist. No CI action |
| **Cekura** (before: Vocera) | Detailed interruption metrics (stop time in ms). Latency P25 to P99 | Phone, SIP, Vapi, Retell, ElevenLabs, LiveKit, Pipecat | $0.25 per minute, or $500 per month | The CI gate is all or nothing |

Sources: [hamming.ai/pricing](https://hamming.ai/pricing), [coval.ai/pricing](https://coval.ai/pricing), [docs.coval.ai](https://docs.coval.ai/concepts/metrics/overview), [getbluejay.ai/pricing](https://getbluejay.ai/pricing), [docs.getbluejay.ai](https://docs.getbluejay.ai/native-integrations/github.md), [getnetra.ai/pricing](https://getnetra.ai/pricing/), [cekura.ai/pricing](https://www.cekura.ai/pricing), [docs.cekura.ai](https://docs.cekura.ai/documentation/key-concepts/metrics/pre-defined-metrics.md).

Others that exist: Roark, Vapi Simulations (Vapi agents only), Retell simulation (text only), LiveKit Agent Simulations (LiveKit Cloud only), Sipfront.

### What we learned from them

PanchoVoiceAI takes good ideas from these tools, and we want to say so:

- A CI gate with a minimum score (Bluejay).
- The whole setup as YAML files in Git (Bluejay, Cekura).
- A scripted caller for exact tests (Cekura, Bluejay).
- Mock tools, so tests do not touch real APIs (Cekura, Bluejay).
- Stop time in milliseconds for interruptions (Cekura).
- Noise on the line (Coval).

Still on our roadmap from the same list: turn a failed real call into a test (Hamming, Cekura), latency per stage (Coval), three level red team verdicts (Bluejay), and an MCP server (Coval, Bluejay, Cekura).

### When a commercial tool is the better choice

- You want phone, SIP and many platforms **today**. PanchoVoiceAI supports `pv-ws/1` WebSocket agents today; the rest is roadmap.
- You want a hosted service with support and no setup.
- You need many languages and noise profiles out of the box.

## Open source tools

| Project | Stars | License | Caller with real audio | Things to know |
|---|---|---|---|---|
| [Pipecat Evals](https://docs.pipecat.ai/pipecat/evals/overview.md) (inside [pipecat](https://github.com/pipecat-ai/pipecat)) | 17,311 | BSD-2 | Yes | Tests Pipecat agents only. No WER, no panel |
| [LangWatch Scenario](https://github.com/langwatch/scenario) | 1,030 | Apache-2.0 | Yes | Scenarios are code, not YAML. The panel is their platform. No WER |
| [DeepEval voice](https://github.com/confident-ai/deepeval) | 19,532 | Apache-2.0 | Yes (beta) | No phone. The panel is paid |
| [ServiceNow EVA](https://github.com/ServiceNow/eva) | 241 | MIT | Yes | A benchmark, not a per agent test tool |
| [decibench](https://github.com/unforkopensource-org/decibench) | 13 | Not confirmed | Yes | Very small, no changes in 5 months, no Windows |
| [livekit_agent_simulator](https://github.com/quangdang46/livekit_agent_simulator) | 7 | MIT | Yes | LiveKit only |
| [fixa](https://github.com/fixadev/fixa) | 116 | BSD-2 | Yes (phone) | No activity since January 2025 |
| [voicetest](https://github.com/voicetestdev/voicetest) | 40 | Apache-2.0 | Mostly text | No latency, no WER |
| [promptfoo](https://github.com/promptfoo/promptfoo) | 27,393 | MIT | No | Has no caller that speaks |

Star counts are for the whole repository. For Pipecat, DeepEval and promptfoo the repository is much larger than its voice testing part.

The closest projects to PanchoVoiceAI are **LangWatch Scenario**, **Pipecat Evals** and **DeepEval voice**. They are healthy and well made.

### When another open source tool is the better choice

- Your agent is a Pipecat agent and you want tests inside the same framework: look at Pipecat Evals.
- You want scenarios as code inside your existing test suite: look at LangWatch Scenario.
- You already use DeepEval for text evals and want voice in the same place: look at DeepEval.
- You want to compare models on a public benchmark: look at ServiceNow EVA.

## Where PanchoVoiceAI is different

| Point | What it means |
|---|---|
| **Self-hosted** | It runs on your own machine. With the commercial tools above, this is an enterprise option or does not exist |
| **Any platform through adapters** | The tool is not tied to one framework. Today: any agent that speaks the small `pv-ws/1` protocol. Planned: HTTP turn agents, Pipecat, LiveKit, Vapi, Retell, ElevenLabs, Twilio |
| **Neutral recording** | PanchoVoiceAI records both channels itself and measures latency, barge-in and WER from that recording. The evidence is the same for every platform and does not depend on what a platform reports |
| **Local brain option** | The AI caller, the judge and Panchito can run on a local model with Ollama. The caller voice (Kokoro) and speech to text (faster-whisper) are local too. A test can run with nothing leaving your network |
| **No markup per minute** | You pay only for the compute or the model keys you choose. The commercial tools above charge about $0.25 to $0.40 per simulated minute |
| **Its own panel and wizard** | A panel with a two channel player, a YAML editor and onboarding, plus a setup wizard. No account on a platform is needed |
| **Panchito** | An AI companion that proposes scenarios and mock data and explains failures. It proposes; you approve |
| **Tool calls are validated** | Exact arguments, forbidden tools, order and final state are checked against a mock tool server |

## Where PanchoVoiceAI is behind

To be fair in both directions:

- It is new. The first version was built in November 2026.
- One adapter today (`pv-ws/1`). No phone, SIP or WebRTC yet.
- Two languages in scenarios today: English and Spanish.
- No load tests, no red team suite, no baseline comparison, no entity accuracy yet.
- The judge is not calibrated against human labels yet. No tool in this comparison, as far as we could find, publishes independent proof of its judge accuracy either.

## Libraries PanchoVoiceAI builds on

| Library | Used for | License |
|---|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Local speech to text | MIT |
| [silero-vad](https://github.com/snakers4/silero-vad) (the ONNX model inside faster-whisper) | Finding speech on the recording | MIT |
| [jiwer](https://github.com/jitsi/jiwer) | WER | Apache-2.0 |
| [kokoro](https://github.com/hexgrad/kokoro) (through a kokoro-fastapi server) | The caller voice | Apache-2.0 |

[pipecat](https://github.com/pipecat-ai/pipecat) (BSD-2) is the planned base for the live transports of later adapters.
