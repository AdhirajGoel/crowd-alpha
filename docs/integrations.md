# Optional research connections

The daily pipeline runs with X, Alpaca and official macro sources. TradingView and Discord extend interactive research in Codex or Claude Code.

## TradingView

Ask which TradingView connection the user can access. Inspect available agent tools or the connector's official installation instructions, then help configure that connection in the selected agent. Use the user's own account and authentication flow. Verify one read-only symbol or chart query and record the actual tool names and permitted scope in `.local/integrations.json`.

Use it to inspect a source's chart thesis, price levels, fundamentals or the user's chosen watchlist when asked. Watchlist and alert changes require a separate user request. A working connection in one agent does not establish availability in the other; verify whichever client the user intends to use.

## Discord

Ask the user to choose an authorized Discord integration and the servers/channels it may read. The available tools and permissions depend on that integration. Use an approved connector or a bot the user can authorize; do not extract session tokens or browser credentials. If the user cannot authorize a suitable connection, leave Discord unconfigured and continue with the core installation.

Record the allowed server/channel IDs locally in `.local/integrations.json`. Verify a narrow read-only query. Search only that scope and retain message dates and links when using claims in an assessment. Do not broaden searches, post messages, or add bots without the user's authority.

## Local integration record

Store only configuration, scope and verification details here; never put tokens in this file. For example:

```json
{
  "tradingview": {"enabled": false},
  "discord": {"enabled": false, "allowed_channels": []}
}
```

Replace the disabled examples only after a real connection is configured and checked. Agent credentials remain in the agent's supported credential store. Optional integrations do not alter the daily report schedule or automatically ingest Discord history.
