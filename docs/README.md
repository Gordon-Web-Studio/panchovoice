# PanchoVoiceAI documentation

Start with the [project README](../README.md) for the pitch and the quick start.

## Get started

| Page | Read it when |
|---|---|
| [Install](install.md) | You set up PanchoVoiceAI on Windows, macOS or Linux |
| [Inicio rápido (español)](es/inicio-rapido.md) | You want the quick start in Spanish |
| [Demo: ACME Appointments](demo.md) | You run the demo agent and want to see the planted bug |
| [Usage](usage.md) | You run suites, read results, or tour the panel |

## Write tests

| Page | Read it when |
|---|---|
| [Scenarios and fixtures](scenarios.md) | You write or edit a scenario or mock data |
| [Metrics](metrics.md) | You want to know how a number is measured and what it means |
| [Panchito](panchito.md) | You want the AI companion to draft scenarios or explain a failure |

## Connect your agent

| Page | Read it when |
|---|---|
| [Adapters and pv-ws/1](adapters.md) | You make your own agent testable, or look for a platform on the roadmap |
| [Build your own dummy agent](build-your-own-dummy-agent.md) | You want to learn how a voice agent is built, step by step |

## Automate

| Page | Read it when |
|---|---|
| [CI gate](ci.md) | You block releases on test results |
| [API](api.md) | You call PanchoVoiceAI from scripts or other apps |
| [MCP server](mcp.md) | You want Claude Code, Claude Desktop or Cursor to run suites and read results |
| [Configuration](configuration.md) | You need a key of `panchovoice.yaml` |

## Background

| Page | Read it when |
|---|---|
| [Comparison](comparison.md) | You compare PanchoVoiceAI with commercial and open source tools |
| [Contract](architecture/contract.md) | You work on the code: file formats, protocol, API |
| [Decisions](architecture/decisions.md) | You want to know why something is the way it is |

## Project files

- [Contributing](../CONTRIBUTING.md)
- [Code of Conduct](../CODE_OF_CONDUCT.md)
- [Security](../SECURITY.md)
- [License (Apache-2.0)](../LICENSE)

## Words used in these docs

| Word | Meaning |
|---|---|
| Agent under test | Your voice agent, the thing being tested |
| Caller | The test agent of PanchoVoiceAI that phones your agent |
| Scenario | One test call, as a YAML file |
| Fixture | The mock data and mock tools behind a scenario, as a JSON file |
| Run | One execution of one scenario |
| Suite | One execution of one or more scenarios |
| Check | One pass or fail rule inside a run |
| Gate | The go or no go rule over a whole suite |
| Barge-in | The caller talks while the agent is still talking |
| WER | Word error rate: the share of words heard wrong |
| P50, P95 | The median, and the value that 95 percent of turns stay under |
| VAD | Voice activity detection: finding where speech is in audio |
| Brain | The LLM used by the AI caller, the judge and Panchito |
