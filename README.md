# Crowd Alpha

**Market intelligence from the people you follow.**

Crowd Alpha reads the X accounts you choose, combines their posts and charts with Alpaca prices and official macro releases, and produces a daily brief with a local evidence archive. Saved views and discussion notes let you follow competing theses and open questions across reports.

[Example brief (PDF)](docs/assets/demo-brief.pdf), produced by the included demo with fictional sources, prices and assessment.

Run it with Codex or Claude Code on macOS. Live use requires your own X API access, Alpaca market-data credentials and access to the selected generation engine.

## Set it up with your agent

Give Codex or Claude Code the repository URL and this instruction:

> Set up Crowd Alpha on my Mac. Read AGENTS.md and follow docs/setup.md. Ask me for my source accounts, engine, spending limit and schedule, help me configure credentials locally, and verify the installation before enabling scheduled collection.

You sign in to your chosen engine and enter secrets in your terminal. Your account roster, credentials and generated research stay out of Git.

## Try the demo

From the repository directory, with Python 3.11 or newer:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python crowd_alpha.py --demo
```

The demo replays synthetic inputs and a supplied example synthesis through the PDF, evidence-archive and memory code, without network or model calls. It prints the output paths and the command to read its saved view and discussion memory.

For interactive research, ask your agent about the saved view or authorize a fresh X lookup. Optional TradingView and Discord connections let the agent inspect charts and conversations alongside that evidence; they are separate from the daily pipeline.

[Setup and commands](docs/setup.md) · [Optional integrations](docs/integrations.md) · [Design and development](docs/design.md)

MIT licensed. This is a static release of a personal research workflow built with AI coding tools.
