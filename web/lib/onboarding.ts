import type { OnboardingStepId } from "./types";

export interface StepMeta {
  id: OnboardingStepId;
  title: string;
  why: string;
  command?: string;
  action?: { label: string; href: string };
  /** Steps the panel cannot detect on its own get an "I did this" button. */
  manual?: boolean;
}

export const STEPS: StepMeta[] = [
  {
    id: "demo_running",
    title: "Start the demo agent",
    why: "ACME Appointments is a small voice agent that runs on your PC. It has a bug on purpose, so your first suite shows a real failure.",
    command: "pv demo",
    action: { label: "Check agents", href: "/agents/" },
  },
  {
    id: "first_run",
    title: "Run your first suite",
    why: "The caller agent phones the demo, talks with real audio, records both channels and scores the call.",
    command: "pv run --scenario book-happy-path",
    action: { label: "Run suite", href: "/?run=1" },
  },
  {
    id: "viewed_report",
    title: "Open a run report",
    why: "Listen to the call, see the latency of every turn, the tool calls with their exact args, and what the judge said.",
    action: { label: "Open latest failed run", href: "/runs/?status=failed" },
  },
  {
    id: "agent_added",
    title: "Connect your own agent",
    why: "Add your agent to panchovoice.yaml. It speaks the pv-ws/1 WebSocket protocol and calls the mock tools.",
    action: { label: "How to add an agent", href: "/agents/#add" },
    manual: true,
  },
  {
    id: "scenario_created",
    title: "Create a scenario",
    why: "Write one YAML file per test. Panchito can draft one for you from your agent prompt.",
    action: { label: "New scenario", href: "/scenarios/edit/?new=1" },
  },
  {
    id: "ci_enabled",
    title: "Turn on the CI gate",
    why: "Run the suite on every pull request. The build fails when the pass rate drops under the gate.",
    command: "pv run --gate --junit report.xml",
    action: { label: "See gate settings", href: "/settings/#gate" },
    manual: true,
  },
];
