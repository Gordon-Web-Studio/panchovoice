# Security

## Report a vulnerability in private

**Do not open a public issue for a security problem.**

Report it in private with GitHub's "Report a vulnerability" button (the Security tab of the repository, under Advisories). If that option is not available, contact the maintainers in private through the GitHub organization page.

Please include:

- What the problem is and where (file, endpoint, command).
- The steps to reproduce it.
- What an attacker could do with it.
- The version or commit you tested.

We will confirm that we got your report, look into it, and tell you what we plan to do. Please give us a fair amount of time to fix the problem before you publish details.

## Never share real customer audio or data

Voice test data can hold personal information.

- **Never put real customer audio in an issue, a pull request, a discussion, or a test fixture.** This includes recordings, transcripts, and screenshots of transcripts.
- Never post real names, phone numbers, account data, API keys or tokens.
- To show a problem, reproduce it with the demo agent and made up data (ACME, Sam Rivera, Jordan Lee), or describe it in words.
- If you posted something private by mistake, tell the maintainers at once so it can be removed. Editing a post does not remove it from the history.

## Things to know when you run PanchoVoiceAI

- **The API server has no login.** `pv serve` is made for your own machine or a trusted network. Do not expose port 8700 to the internet. API tokens are planned.
- **Recordings are stored as plain files** in `data/runs/`. If you test with real audio, protect that folder like any other personal data, and do not upload it as a public CI artifact.
- **Keys stay out of files.** The Anthropic key is read from the env variable `ANTHROPIC_API_KEY`. Never write a key into `panchovoice.yaml`, a scenario, a fixture or a workflow file. Use your CI system's secret store.
- **A cloud brain sees the transcripts.** With the `claude-cli` or `anthropic` provider, call transcripts go to that service for the judge and for Panchito. With `ollama`, they stay on your machine.
- **The mock tool server is for tests.** It is not a real back end and has no access control.
- **Scenario and fixture files are data, but review them.** Review files from people you do not know before you run them against an agent that can reach real systems. Point test agents at the mock tools, not at production APIs.

## Supported versions

The project is new. Security fixes go into the latest version on the main branch.
