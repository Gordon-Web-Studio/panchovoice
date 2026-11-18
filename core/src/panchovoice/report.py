"""Reports: JUnit XML and a rich console summary."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from rich.console import Console
from rich.table import Table


def junit_xml(suite: dict[str, Any], results: list[dict[str, Any]]) -> str:
    total_s = sum((r.get("duration_ms") or 0) for r in results) / 1000
    ts = ET.Element("testsuites", name="panchovoice", tests=str(len(results)),
                    failures=str(sum(r["status"] == "failed" for r in results)),
                    errors=str(sum(r["status"] == "error" for r in results)), time=f"{total_s:.3f}")
    s = ET.SubElement(ts, "testsuite", name=suite.get("suite_id", "suite"), tests=str(len(results)),
                      failures=str(sum(r["status"] == "failed" for r in results)),
                      errors=str(sum(r["status"] == "error" for r in results)),
                      timestamp=suite.get("started_at", ""), time=f"{total_s:.3f}")
    props = ET.SubElement(s, "properties")
    for k in ("pass_rate", "gate_passed", "gate_detail"):
        ET.SubElement(props, "property", name=k, value=str(suite.get(k)))
    seen: dict[str, int] = {}
    for r in results:
        n = seen.get(r["scenario_id"], 0)
        seen[r["scenario_id"]] = n + 1
        name = r["scenario_id"] if n == 0 else f"{r['scenario_id']} #{n + 1}"
        tc = ET.SubElement(s, "testcase", classname=f"panchovoice.{r.get('agent_id') or 'agent'}", name=name,
                           time=f"{(r.get('duration_ms') or 0) / 1000:.3f}")
        if r["status"] == "failed":
            bad = [c for c in r.get("checks", []) if c.get("status") == "failed"]
            msg = "; ".join(f"{c['id']}: {c['detail']}" for c in bad)[:500]
            f = ET.SubElement(tc, "failure", message=msg or "failed", type="check")
            f.text = "\n".join(f"[{c['id']}] {c['detail']}" + (f"\n  evidence: {c['evidence']}" if c.get("evidence") else "")
                               for c in bad)
        elif r["status"] == "error":
            errs = [c for c in r.get("checks", []) if c.get("status") == "error"]
            msg = r.get("error") or "; ".join(f"{c['id']}: {c['detail']}" for c in errs) or "harness error"
            ET.SubElement(tc, "error", message=msg[:500], type="harness")
        out = ET.SubElement(tc, "system-out")
        m = r.get("metrics") or {}
        out.text = (f"run_id={r['run_id']} p50={m.get('latency_p50_ms')} p95={m.get('latency_p95_ms')} "
                    f"wer={m.get('wer')} barge_in_stop_ms={m.get('barge_in_stop_ms')}")
    ET.indent(ts)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(ts, encoding="unicode") + "\n"


_STATUS = {"passed": "[green]passed[/]", "failed": "[red]failed[/]", "error": "[yellow]error[/]"}


def _ms(v: Any) -> str:
    return "-" if v is None else f"{v}"


def print_summary(suite: dict[str, Any], results: list[dict[str, Any]], console: Console | None = None) -> None:
    con = console or Console()
    t = Table(title=f"Suite {suite.get('suite_id')}", show_lines=False)
    for col in ("scenario", "status", "p50 ms", "p95 ms", "WER", "barge-in ms", "failed checks / error"):
        t.add_column(col)
    for r in results:
        m = r.get("metrics") or {}
        bad = [c["id"] for c in r.get("checks", []) if c.get("status") in ("failed", "error")]
        why = r.get("error") or ", ".join(bad)
        w = m.get("wer")
        t.add_row(r["scenario_id"], _STATUS.get(r["status"], r["status"]), _ms(m.get("latency_p50_ms")),
                  _ms(m.get("latency_p95_ms")), "-" if w is None else f"{w:.2f}", _ms(m.get("barge_in_stop_ms")),
                  why[:80])
    con.print(t)
    pr = suite.get("pass_rate")
    gate_txt = "[green]PASSED[/]" if suite.get("gate_passed") else "[red]FAILED[/]"
    con.print(f"passed {suite.get('passed', 0)}, failed {suite.get('failed', 0)}, errors {suite.get('errors', 0)}, "
              f"pass rate {'-' if pr is None else f'{pr:.0%}'}; gate {gate_txt} ({suite.get('gate_detail', '')})")
