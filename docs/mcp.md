# MCP server

PanchoVoiceAI has an MCP server. MCP (Model Context Protocol) is the standard way a coding agent, such as Claude Code, Claude Desktop or Cursor, calls outside tools. With it, the coding agent can list and edit your scenarios, run a suite against your voice agent, read the results, and ask Panchito why a run failed.

The server talks over stdio: the coding agent starts `pv mcp` as a child process and sends messages on its stdin and stdout. Nothing listens on a network port, except the mock tools server that a suite needs (see [Ports](#ports)).

## Start it

```bash
uv run --project <path>/core pv mcp --config <path>/panchovoice.yaml
```

Always pass `--config`. The coding agent can start the server in any folder, and without `--config` PanchoVoiceAI looks for `panchovoice.yaml` in that folder and its parents.

| Option | Meaning |
|---|---|
| `--config PATH` | The `panchovoice.yaml` of your project (a file, or the folder that holds it) |
| `--mock-port N` | Port of the mock tools server for `run_suite`. Default `0`: a free port |

## Add it to your coding agent

Replace `<path>` with the folder of your PanchoVoiceAI checkout, for example `/home/sam/panchovoice` or `C:/Users/sam/panchovoice`.

### Claude Code

```bash
claude mcp add panchovoice -- uv run --project <path>/core pv mcp --config <path>/panchovoice.yaml
```

Then run `/mcp` in Claude Code to see the server and its tools.

### Claude Desktop

Edit `claude_desktop_config.json` (Settings, Developer, Edit Config):

```json
{
  "mcpServers": {
    "panchovoice": {
      "command": "uv",
      "args": ["run", "--project", "<path>/core", "pv", "mcp", "--config", "<path>/panchovoice.yaml"]
    }
  }
}
```

Restart Claude Desktop after the change.

### Cursor

Edit `.cursor/mcp.json` in your project (or `~/.cursor/mcp.json` for all projects):

```json
{
  "mcpServers": {
    "panchovoice": {
      "command": "uv",
      "args": ["run", "--project", "<path>/core", "pv", "mcp", "--config", "<path>/panchovoice.yaml"]
    }
  }
}
```

If `uv` is not found, put the full path of `uv` in `command`.

## Tools

Every tool returns JSON. A tool that cannot do its job (for example an unknown run id) returns an error with a plain message.

| Tool | Arguments | Returns |
|---|---|---|
| `list_scenarios` | `tag` (optional) | `[{id, title, risk, tags, agent, mode, valid}]` |
| `get_scenario` | `id` | `{id, yaml, file, valid, errors}` |
| `validate_scenario` | `yaml` | `{ok, errors: [{path, message}]}`. Checks the schema, the agent id and the fixture |
| `save_scenario` | `id`, `yaml`, `confirm` (default `false`) | A preview, or the write result. See [The confirm rule](#the-confirm-rule) |
| `run_suite` | `scenario_ids`, `tags`, `runs` (all optional) | `{suite_id, status, total, scenario_ids}` at once; the suite runs in the background |
| `get_suite` | `suite_id` | Status, progress, pass rate, gate, and one row per run (status, p50, p95, WER, failed checks) |
| `gate_status` | `suite_id` (optional, default the newest suite) | `{pass_rate, gate_passed, reasons, threshold, ...}` |
| `list_runs` | `limit` (default 20, max 200), `scenario_id` (optional) | Recent runs, newest first |
| `get_run` | `run_id` | Compact result: metrics, failed checks with evidence, turns, mock tool calls, final state |
| `explain_run` | `run_id`, `question` (optional) | `{run_id, reply, proposals}` from Panchito |

A typical flow: `list_scenarios`, then `run_suite`, then `get_suite` again and again until `status` is not `running`, then `get_run` or `explain_run` on the failed runs, then `gate_status`.

Suite `status` is `running`, `finished`, `error` (the suite crashed) or `interrupted` (the process that ran it stopped). A run takes from about 20 seconds to 3 minutes, because it is a real call with real audio.

`explain_run` uses the LLM of your `panchovoice.yaml` (the `llm` key), the same brain as [Panchito](panchito.md) in the panel. It can take up to 2 minutes.

## The confirm rule

The MCP server never writes a file, except through `save_scenario` with `confirm=true`.

1. The coding agent calls `save_scenario` with `confirm=false` (the default). Nothing is written. The reply has the target file, the validation result, a diff against the current file, and the message "call again with confirm=true to write".
2. The coding agent shows the preview to you.
3. Only after you say yes, it calls `save_scenario` again with `confirm=true`. The YAML is validated again, and written only when it is valid (the same rule as `PUT /api/scenarios/{id}`).

Run results, the sqlite index and the suite files in `data_dir` are written by the runner during a suite, as with `pv run`.

## One suite at a time

`run_suite` refuses to start when a suite already runs in this server. It also refuses when another PanchoVoiceAI process (for example `pv serve`) runs a suite right now: sqlite says `running` and its suite file changed in the last 10 minutes. Two suites at the same time would call the same agent at the same time.

## Ports

A suite needs the mock tools server: the agent under test calls its mock tools there. The MCP server starts it on the first `run_suite`, on a free port by default, so it does not collide with `pv serve` on 8700. The agent gets the right url in the `start` message, as always.
