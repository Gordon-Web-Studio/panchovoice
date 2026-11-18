# panchovoice (core)

The core engine of PanchoVoiceAI: scenarios, mock tools, the audio caller, the pv-ws/1 adapter, metrics, the judge, the runner and the `pv` CLI.

```
uv sync
uv run pv --help
uv run pv validate
uv run pv run --scenario book-happy-path
uv run pytest
```

Commands run from this folder find `panchovoice.yaml` in the current folder or its parents (the repo root has one).
