# How Crowd Alpha works

The project began with a practical problem: useful market observations were spread across accounts and conversations, and reading them individually made it hard to maintain a coherent view. The workflow collects chosen sources, checks their claims against dated evidence, and preserves the questions that deserve another look.

## Daily pipeline

1. **Collect and retain evidence.** Retrieve the configured X accounts and one level of referenced context. A local lock coordinates scheduled and on-demand retrieval; the spending ledger reserves request costs before sending. Retries reuse completed responses.
2. **Triage and verify.** The selected engine identifies relevant posts and chart images. Alpaca supplies stock/ETF price history; official sources supply Treasury yields, inflation releases and the latest available Fed statement.
3. **Synthesize with context.** Codex or Claude Code receives the captured evidence and prior memory. It returns a brief, source mappings, updated views across three horizons, and prospective observations to follow. Structured-output and evidence-reference checks run before publication.
4. **Publish and remember.** Render the PDF, preserve the inputs and outputs in a hashed local archive, and update the current market view. Historical recovery cannot replace newer memory.

The brief has a 480-word ceiling, including its title and date. The evidence archive and internal memory preserve supporting detail. Deterministic checks establish that referenced inputs exist and the artifact is internally consistent; the reader can inspect the underlying evidence when judging an interpretation.

## Human and AI contributions

The human chooses the research problem, source universe, questions, spending limits and interpretation goals. AI coding tools helped implement and revise the collection, synthesis and verification workflow. During use, the generation engine connects evidence and proposes interpretations; source selection and decisions remain with the user.

The design preserves disagreement and conditions instead of treating repeated commentary as independent confirmation. It records when a source changes its view and keeps user hypotheses distinct from agent conclusions. Prospective directional observations use later daily closes; they are not a trading-return backtest.

## Code map

| File | Responsibility |
| --- | --- |
| `crowd_alpha.py` | Command entry point |
| `src/onboarding.py` | Configuration, local credentials, checks and scheduling |
| `src/market_brief.py` | Collection, synthesis, PDF generation and recovery queue |
| `src/engine.py` | Isolated Codex/Claude Code generation and JSON validation |
| `src/macro_data.py` | Dated official macro-source retrieval |
| `src/provenance.py` | Evidence mappings, archives and integrity checks |
| `src/live_memory.py` | Market views and discussion records |
| `src/query_x.py` | On-demand retrieval sharing the daily collection lock |
| `src/demo.py` | Offline synthetic replay |

The engine adapters follow the official [Codex non-interactive interface](https://developers.openai.com/codex/noninteractive) and [Claude Code programmatic interface](https://code.claude.com/docs/en/headless). They reuse the user's authenticated CLI, request structured output, and keep X/Alpaca credentials out of the generation process environment. Claude generation disables tools and customizations; Codex uses an isolated directory, ignored user configuration and a read-only sandbox.
