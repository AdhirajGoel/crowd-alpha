# Setup on macOS

Use Python 3.11 or newer and a current Codex or Claude Code CLI. Setup works with either agent. The chosen generation engine needs an authenticated account with access to the selected model; its usage is billed or metered by that provider.

## 1. Gather the user's choices

Ask for the generation engine and model, 1–20 X handles, timezone, daily report hour, monthly X spending ceiling, and a conservative per-resource X cost. The cost must cover the most expensive post, user or media resource the installation will request. Confirm it against the user's [X API billing configuration](https://docs.x.com/x-api/getting-started/pricing); do not silently assume an old rate. Confirm permission for the initial account lookup and live verification within that ceiling. Ask whether TradingView or Discord is wanted, and whether to enable a schedule after verification.

These choices belong in local configuration. Do not copy the repository author's preferences or choose accounts for the user.

## 2. Install and demonstrate

Clone the repository into the user's chosen directory. Check `python3 --version`, `codex --version` and `claude --version` as applicable. Install only the selected engine if it is missing, using its official instructions: [Codex CLI](https://developers.openai.com/codex/cli/) or [Claude Code](https://code.claude.com/docs/en/setup). Help the user complete the provider's login flow themselves.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python crowd_alpha.py --demo
```

Open the resulting PDF and explain that the demo uses supplied synthetic inputs and synthesis. It verifies local reporting and memory without consuming API or model allowance. The command prints the evidence index and an exact command to read that demo’s saved view and question. Use its printed `CROWD_ALPHA_HOME` when discussing the demo; the default memory command reads the separate live installation.

## 3. Configure the installation

Use the user's values in the command below. The uppercase values are placeholders; never execute them literally.

```sh
.venv/bin/python crowd_alpha.py --setup \
  --engine ENGINE --model MODEL --accounts HANDLE_1 HANDLE_2 \
  --x-monthly-usd MONTHLY_CAP --x-resource-usd RESOURCE_COST \
  --timezone TIMEZONE --hour HOUR
```

`ENGINE` is `codex` or `claude`. Defaults, when `--model` is omitted, are `gpt-6-astra` for Codex and `opus` for Claude. Both use high reasoning effort. Choose a model available to the user's account that supports images and structured output. The CLI executable is resolved to an absolute path for scheduled use.

Setup creates `.local/config.json` and refuses to overwrite an existing configuration. To change an installation, stop its schedule, edit the existing local config, and verify it again. Preserve `x-budget.json`; deleting it would reset the local accounting.

## 4. Enroll credentials locally

The user needs X developer API access with the required recent-search, post and user lookup permissions, and Alpaca stock market-data credentials. Only market-data endpoints are used.

Have the user run this in their own terminal:

```sh
.venv/bin/python crowd_alpha.py --credentials
```

It prompts without echoing and stores three items in macOS Keychain: `crowd-alpha.x-bearer-token`, `crowd-alpha.alpaca-key-id`, and `crowd-alpha.alpaca-secret`. Do not collect these values in chat. Existing credentials can alternatively be supplied through `X_BEARER_TOKEN`, `APCA_API_KEY_ID`, and `APCA_API_SECRET_KEY`; Keychain is preferable for the scheduler, which does not inherit an interactive shell's environment.

## 5. Verify, then run

After the user authorizes the X lookup, resolve their handles into a local ID mapping:

```sh
.venv/bin/python crowd_alpha.py --resolve-accounts
.venv/bin/python crowd_alpha.py --check
.venv/bin/python crowd_alpha.py --smoke-engine
.venv/bin/python crowd_alpha.py
```

The account lookup consumes X API resources. `--check` validates local configuration, dependencies, CLI availability and credential presence without a network request. `--smoke-engine` makes a small structured-generation call with the selected model. The final command collects live sources for the latest completed daily window ending at the configured report hour, using the configured X ceiling and the engine's allowance. Before that hour, it reports the previous day. Use `--test-run` for a rolling 24-hour snapshot ending now; its separate PDF and archive do not mark a scheduled day complete.

Verify the PDF, the dated evidence archive, and `.local/status.json`. Run `--verify-provenance YYYY-MM-DD` for the resulting date. Do not describe a mocked or offline check as a live provider check.

## 6. Optional scheduling and integrations

Only after the user requests scheduled collection:

```sh
.venv/bin/python crowd_alpha.py --schedule
```

The LaunchAgent checks every 30 minutes and at login, collecting each due date after the configured hour. The Mac must be awake and logged in. Missed dates remain queued for retry; sources must still be available for the historical window. To disable it, use `--unschedule`. The installer refuses to replace an existing Crowd Alpha LaunchAgent.

Follow [integrations.md](integrations.md) for optional interactive TradingView and Discord access. No personal email integration is included.

## Working commands and files

| Task | Command or location |
| --- | --- |
| Run the latest completed daily window | `.venv/bin/python crowd_alpha.py` |
| Retry unfinished dates | `.venv/bin/python crowd_alpha.py --backfill` |
| Create a fresh rolling 24-hour snapshot | `.venv/bin/python crowd_alpha.py --test-run` |
| Read recent X posts | `.venv/bin/python src/query_x.py --hours 6` |
| Read analyst memory | `.venv/bin/python src/live_memory.py` |
| Verify an archive | `.venv/bin/python crowd_alpha.py --verify-provenance YYYY-MM-DD` |
| Offline checks | `.venv/bin/python crowd_alpha.py --self-check` |
| Reports | `.local/reports/` |
| Evidence and generation records | `.local/provenance/` |
| Current state and discussion notes | `.local/live/` |
| Pending work | `.local/queue.json` |

The spending ledger reserves a conservative amount before each X request and retains reservations for uncertain failures. It covers this installation's requests, not other applications on the account. Configure the provider's own spending controls as well. Keep the state directory intact between runs. `CROWD_ALPHA_HOME` can select a separate state directory for an isolated installation or test.
