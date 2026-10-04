# Remora Desk

**An autonomous options desk that cannot hurt itself — and explains every
decision it makes, including the ones not to trade.**

Built for the Alpaca AI Trading Agents Hackathon. Trades **debit vertical
spreads on SPY** in a paper account. Maximum loss per trade is the premium paid,
by construction: the loss is bought up front.

---

## The idea

A live-lab gold trader bottled his discipline into three layers with a strict
authority hierarchy:

| Layer | What it does | Authority |
|---|---|---|
| **Signal** (deterministic) | Clean-cross breakout of a 30-min channel + multiframe RSI + regime filter | Emits GO / NO-GO |
| **Decision** (an LLM) | Receives a snapshot of facts, converts GO into OPEN or PASS, writes the reasoning | May only *narrow*. Never opens without a signal, never touches exits or sizing |
| **Risk officer** (deterministic) | 10 hard pre-trade gates + a 60-second exit monitor | Overrules everything, **including the AI** |

Any failure of the AI layer — timeout, malformed JSON, no key — resolves to
**PASS**. The desk would rather skip a trade than trade blind.

### Why it can't hurt itself

- Only **debit spreads**. Max loss = premium prepaid. No naked premium ever.
- **Never market orders** on options; always multi-leg limit.
- **Flat by 15:50 ET** every day. No overnight, no gap risk, no assignment risk.
- Hard caps: ≤ 8 trades/day, ≤ 3 open positions, ≤ $500 premium/trade,
  ≤ $1,500 aggregate.
- Two circuit breakers: **−1.5% on the day** halts until tomorrow; **−4%
  cumulative** halts entirely and only a human can re-arm it.
- A 30-minute cooldown after any losing close — no revenge trades.

### Why the journal is the product

Four days of P&L is noise. What is *not* noise is a complete, timestamped
record of every decision and the reasoning behind it. `decisiones.jsonl`
records **every cycle, including the PASSes** — with the full G1–G10 gate
table, the LLM's verbatim reasoning, and what the desk chose to do. Gates that
were never reached are recorded as `NOT_EVALUATED`, not as passes: *"the gate
passed"* and *"the gate was never evaluated"* are different facts.

---

## Quickstart

```bash
# 1. Credentials (paper account only)
cp .env.example .env        # then fill in your keys

# 2. Verify the account, and that options level 3 is approved
python smoke_test.py

# 3. Look at what the signal would have done on real bars (read-only)
python validar_senal.py --dias 5

# 4. Verify the option chain, IV and G10 without creating an order
python validar_contrato.py

# 5. Run the desk in shadow mode: it decides and journals, but never orders
.\lanzar.cmd sombra
```

## Running the desk

`lanzar.cmd` is the supervised launcher and **the way this desk was actually run
during the hackathon**. Use it to reproduce the run faithfully.

```bash
.\lanzar.cmd            # uses MODO from .env (defaults to shadow)
.\lanzar.cmd sombra     # forces shadow: decides and journals, never orders
.\lanzar.cmd real       # posts multi-leg orders to the PAPER account
```

### `sombra` vs `real`

| | `sombra` (shadow) | `real` |
|---|---|---|
| Reads live market data | yes | yes |
| Runs signal, gates and the LLM | yes | yes |
| Writes the full journal | yes | yes |
| **Posts orders** | **never** | yes — to the Alpaca **paper** account |

**`real` does not mean real money.** It means real *orders*, placed against
`paper-api.alpaca.markets` with simulated funds. `ALPACA_PAPER_TRADE=true` is
enforced at startup and the config validator refuses to run without it; no code
path in this repository can reach a live-money endpoint.

`sombra` is the default and the only mode this codebase will start on its own.
`real` requires either an explicit `--modo real` or an interactive `[S/N]`
confirmation at the launcher prompt — it is never entered by accident.

### What the launcher adds

Running the module bare (`python -m motor.agente --modo real`) works, but it is
unsupervised. `lanzar.cmd` wraps it in a restart loop, which is what makes an
unattended multi-hour session survivable:

| Exit code | Meaning | Launcher does |
|---|---|---|
| `0` | clean stop | stops |
| `2` | invalid configuration | stops — a restart cannot fix it |
| `3` | lock held by another engine | stops — a second engine would duplicate orders |
| `4` | needs a human: account error, ambiguous reconciliation, or persisted positions in a non-`real` mode | stops |
| anything else | crash | restarts after 10 s |

A process lock (`diario/motor.lock`) guarantees only one engine ever owns the
account, so relaunching over a live engine fails cleanly with exit code `3`
instead of double-ordering. On every start the executor reconciles open orders
and positions against the broker before it is allowed to do anything else.

### Not on Windows

`lanzar.cmd` is a Windows batch file; the engine itself is platform-independent.
Run it directly and supply your own supervision:

```bash
python -m motor.agente --modo sombra
python -m motor.agente --modo real     # no confirmation, no restart loop
```

### Other entry points

```bash
python -m motor.agente --un-ciclo      # a single decision cycle, then exit
python run_tests.py                    # the full unit suite
python reporte.py                      # refresh docs/index.html from journal
```

## Requirements

**Python 3.9+ and nothing else.** The engine in `motor/` uses only the standard
library — `urllib`, `json`, `threading`, `math`. No pip installs, no
supply-chain surface, no dependency conflicts. Judges can audit every line.

The Alpaca CLI was evaluated and deliberately rejected: it is in alpha and
documented as *"should not be depended on in production"*. Order execution goes
through the REST Trading API directly. The **MCP requirement is met with an MCP
server** (`.mcp.json` mounts `alpaca-mcp-server`), used for the morning
pre-flight, the daily report and the demo video — deliberately **outside** the
execution path.

## Layout

```
motor/
  reloj.py       ET market clock (US DST rules implemented directly — no tzdata)
  config.py      frozen parameters, single source of truth
  rest.py        urllib REST client, retry + backoff, never raises
  datos.py       1m bars -> local 5/15/30m aggregation (closed bars only)
  indicadores.py EMA, Wilder RSI, Wilder ATR — pure functions
  senal.py       S1..S6 clean-cross signal
  gates.py       G1..G10 risk gates + position sizing
  contratos.py   option chain -> $2-wide vertical, liquidity screen
  llm.py         Featherless (OpenAI-compatible), strict JSON, failure => PASS
  ejecutor.py    multi-leg limit orders, idempotent, reconciling
  cartera.py     positions, marks, peaks, P&L
  salidas.py     R1..R7 exits: fib ladder, stall harvest, SL, time-stop, EOD
  diario.py      JSONL journal + atomic state (survives kill -9)
  agente.py      entry point: clock, 10-min cycle, 60-second monitor
tests/           unit suite (stdlib unittest)
reporte.py       JSONL journal -> static GitHub Pages dashboard
docs/            generated, dependency-free dashboard
```

## Documents

- `ARQUITECTURA.md` — system architecture and increments *(Spanish, frozen)*
- `LOGICA_TRADING.md` — trading logic and every parameter *(Spanish, frozen)*
- **`DESVIACIONES.md`** — every place the implementation departs from the frozen
  spec, and why. Including defects found in the spec itself that still need a
  human decision. Worth reading: it is the honest record.

## Status

Paper trading only. `ALPACA_PAPER_TRADE=true` is enforced at startup — the
config validator refuses to run otherwise.

Verified against the hackathon account on 2026-09-01: account active, options
approval/trading level 3, real option-chain snapshots available, and a full
shadow decision cycle completed without posting an order. Five-session signal
validation found 22 raw GO signals and 9 that align with the actual 10-minute
action cadence (1.8 actionable signals/session before G1–G10).

`docs/index.html` is generated from the real local journal. Publishing remains
an explicit repository-owner step (enable GitHub Pages with `/docs` as source).

## License

MIT — see `LICENSE`.
