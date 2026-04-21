# Nifty Options Dashboard

A real-time derivatives engine that reads the Groww Trade API every 5 seconds and converts raw market data into a single actionable trade decision — which Nifty 50 option to buy (or whether to wait), at what strike, for what premium, and with what maximum loss.

---

## What It Does

The dashboard answers one question on every refresh:

> **Should I buy a CE, buy a PE, sell a straddle, wait, or stay out?**

When conditions align it shows:

```
Strategy:  BUY CE — ATM — FULL SIZE
Strike:    24500
Premium:   ₹ 142.50
Max Loss:  ₹ 9,262 / lot

LONG gravity (LS +0.4120) · 4/5 signals confirm · IV NORMAL
```

When they don't it shows **NO TRADE**, **WATCH**, **WAIT** (with the reason — biased, conflicted, or data unreliable), or **SKIP** — and explains why.

---

## Dashboard Walkthrough

The dashboard has five sections from top to bottom.

---

### Section 1 — Options Strategy *(the only panel you act on)*

This is the trade recommendation. Everything else on the dashboard feeds into this.

| Field | What it means |
|---|---|
| **Strategy** | e.g. `BUY CE — ATM`, `BUY PE — 1-OTM`, `SELL STRADDLE — ATM`, `WATCH — prepare BUY CE 1-OTM @ 24550`, `WAIT — LONG BIAS`, `WAIT — CONFLICTED`, or `NO TRADE` |
| **Strike** | The specific Nifty strike to trade |
| **Premium** | Current LTP of that option — what you pay per unit |
| **Max Loss / Lot** | Premium × lot size (65 units). The most you can lose on one lot if the option expires worthless |
| **Size Note** | `FULL SIZE` / `REDUCED SIZE` — how much of your normal position to take |
| **Rationale** | One-line explanation: direction, confidence score, IV level, or the specific blocking condition |
| **IV Regime** | `LOW` / `NORMAL` / `ELEVATED` / `HIGH` — how expensive options are right now |
| **DTE Warning** | Red banner on expiry day or the day before. Theta destroys buyers near expiry. |

**Color code:**
- Green card = BUY signal (CE or PE)
- Amber card = WAIT / WATCH / SELL premium (structure present but not actionable yet)
- Red card = CONFLICTED (forces oppose each other)
- Grey card = NO TRADE / SKIP

**Decision rules in plain English:**

| Situation | Output |
|---|---|
| Strong gravity (`|LS| > 0.35`) + score ≥ 4 + IV normal | BUY CE/PE — **ATM**, **FULL SIZE** |
| Strong gravity + score ≥ 4 + IV elevated (options expensive) | BUY CE/PE — **1-OTM**, **REDUCED SIZE** |
| Strong gravity + score = 3 | BUY CE/PE — **1-OTM**, **REDUCED SIZE** |
| Strong gravity + score = 2 | **WAIT — LONG/SHORT BIAS** (structure present, awaiting more confirmation) |
| Weak gravity (`0.15 < |LS| ≤ 0.35`) + score ≥ 3 | **WATCH — prepare BUY** at the anticipated strike |
| Weak gravity + score < 3 | **NO TRADE** |
| PCR opposes LS direction (near-ATM PCR < 0.8 for LONG, > 1.3 for SHORT) | **WAIT — CONFLICTED** — forces in disagreement |
| Data unreliable (theo vs microprice diverge > 50 pts, or chain warnings) | **WAIT — DATA UNRELIABLE** |
| Expiry day (DTE = 0) | **NO TRADE** — theta risk too high for buyers |
| No direction + IV elevated + DTE ≥ 2 | SELL STRADDLE — ATM (optional premium collection) |

**IV thresholds:**
- `< 12%` → LOW
- `12–18%` → NORMAL
- `18–25%` → ELEVATED — prefer OTM strikes, reduced size
- `> 25%` → HIGH — consider selling premium instead of buying

---

### Section 2 — LS Signal

The LS Factor is the primary directional indicator. It measures how far the expiry anchor is from current spot, scaled to the current volatility of the options market.

```
LS = (Expiry Fair − Spot) / Straddle
```

- **Expiry Fair** = `Max Pain × 0.6 + OI Midpoint × 0.4` — the settlement gravity anchor.
- **Straddle** = ATM call + put premium. Normalises the signal so a 100-pt gap means more at low IV than at high IV and the thresholds (±0.35) are scale-invariant across Nifty levels.

**How to read the LS value:**

| LS Value | Direction | Meaning |
|---|---|---|
| `> +0.35` | LONG | Strong upside gravitational pull toward expiry |
| `+0.15 to +0.35` | WEAK LONG | Mild upside bias — WATCH candidate, not ENTER |
| `−0.15 to +0.15` | FLAT | No directional gravity |
| `−0.15 to −0.35` | WEAK SHORT | Mild downside bias — WATCH candidate |
| `< −0.35` | SHORT | Strong downside gravitational pull |

Below the LS value you see the **Decision** card (ENTER / WAIT / WATCH / SKIP / NO TRADE / CONFLICTED) and the **Confidence panel** (0–5 score).

#### Confidence Score (0–5 checks)

Five independent checks confirm or deny the LS direction. Score ≥ 4 is required for full-size entry; ≥ 3 permits reduced size.

| Check | Passes when… |
|---|---|
| **`futures_bias`** | Futures excess basis + OI change agree with LS direction (e.g. LONG needs `BULLISH` bias label) |
| **`strong_magnet`** | All three hold: `pain_depth > 1.5` **AND** OI concentrated at a wall (`HHI > 0.08` on CE or PE) **AND** max-pain PCR agrees with LS (`≥ 1.3` for LONG, `≤ 0.7` for SHORT). Missing data on any component is treated as "don't penalise." |
| **`regime_aligned`** | Regime bias from the three-gap classifier matches LS (`LONG` / `COUNTER_TREND` labels pass for LONG; `SHORT` / `WITH_TREND` for SHORT) |
| **`expiry_pull`** | Spot is at least 30 pts on the *opposite* side of max pain from the LS direction, i.e. max pain is genuinely pulling price toward LS |
| **`pcr_aligned`** | **Near-ATM PCR** (±3 strikes around true ATM) aligns: `> 1.1` for LONG, `< 0.9` for SHORT. Falls back to total-chain PCR if the near-ATM value isn't available. |

> Why near-ATM PCR, not total-chain? On Nifty the total-chain PCR is permanently inflated by deep OTM institutional put-hedges that have nothing to do with directional positioning. The ±3-strike window cuts through that structural noise.

**CONFLICTED override:**
Even before scoring, the decision is gated by a directional-conflict check:
- LONG + near-ATM PCR < 0.8 → **CONFLICTED** (bearish put writers contradict bullish gravity)
- SHORT + near-ATM PCR > 1.3 → **CONFLICTED** (bullish put writers contradict bearish gravity)

When conflicted, the action is `WAIT — CONFLICTED` regardless of score. The engine will not buy in the face of explicit opposition.

---

### Section 3 — Session Gaps

Three gap values quantify where the market stands relative to its anchors.

| Gap | Formula | What it tells you |
|---|---|---|
| **IG — Intraday Gap** | `Spot − Theoretical Price` | Is spot cheap or rich vs. its fair value *today*? Negative = cheap. |
| **EG — Expiry Gap** | `Spot − Max Pain` | How far is spot from where writers need it to settle? Positive = gravitational pull down. |
| **AG — Alignment Gap** | `Theoretical Price − Max Pain` | Are today's fair value and expiry anchor aligned (< 80 pts) or diverged (> 80 pts = trending day)? |

---

### Section 4 — Key Levels

| Card | What it shows |
|---|---|
| **Spot** | Current Nifty price and whether it is at fair, rich, or cheap vs. theoretical price |
| **True ATM** | Strike with the tightest CE/PE IV spread (often 50–100 pts below spot due to Nifty's structural put skew) |
| **Max Pain** | Strongest expiry settlement anchor |
| **Straddle Range** | `Lower – Upper` bounds for the current expiry (±straddle from ATM) |
| **OI Corridor** | `Put Support – Call Resistance` from dominant OI strikes |
| **PCR (OI)** | Three readouts: total-chain, **Near-ATM** (directional signal), **MaxPain PCR** (writer defence at the pin). Near-ATM > 1.1 = put-heavy near spot; MaxPain > 1.3 = put-writer floor, < 0.7 = call-writer ceiling |

---

### Section 5 — Visual Option Chain (ATM ±3)

Seven strikes nearest the true ATM with:
- **OI bars** — horizontal bars proportional to open interest. Long red on the call side = call writing (resistance); long cyan on the put side = put writing (support).
- **Call LTP / Put LTP** — current last-traded prices
- **Strike** — ATM highlighted in white/cyan
- ITM cells get a subtle background tint

---

## Header Bar

| Element | Meaning |
|---|---|
| **Green dot / CONNECTED** | Live data feed active, refreshing every 5 seconds |
| **Amber dot** | Initializing or authenticating |
| **Red dot / RECONNECTING** | API error — last data may be stale |
| **Amber / Market Closed** | Outside 09:15–15:30 IST — poller is in slow-mode (60s), no error |
| **OPT EXP** (dropdown) | Override the option expiry. **AUTO** = nearest non-expired. List covers today through end of current month (extends to next month's end when within the last week). Amber border = override active. |
| **FUT EXP** (dropdown) | Override the futures expiry. Same AUTO + list semantics as OPT EXP. |
| **LIVE · HH:MM:SS** | Timestamp of last successful data refresh |

A red banner appears at the top of the page if data is stale (> 60 s without refresh) — **do not trade from stale data**.

**Why a dropdown?** Groww sometimes stops serving strike data for the current-week chain *before* 3:30 PM on expiry day. The dropdown lets you fail over to next week's expiry without restarting the app. After 15:30 the auto-discovered expiry automatically rolls forward as well.

---

## Engine — How the Numbers Are Computed

### Module 1 — True ATM Detection

Not the nearest 50-pt strike. The strike where `|call_iv − put_iv|` is smallest — the market's actual 50/50 pricing centre. Guards: falls back to listed ATM when IVs are all identical (weekend/closed data); true ATM capped within 3 strikes (150 pts) of listed ATM; high-IV strikes (> 100%) filtered as data junk.

### Module 2 — Theoretical Price (OI-Weighted PCP)

Put-call parity across the 8 strikes nearest ATM, weighted by `min(CE_OI, PE_OI)` so low-OI strikes drop out automatically. Cross-validated against:
- **Futures microprice** — order-book volume-weighted mid from bid/ask/quantities
- **Breeden-Litzenberger** — risk-neutral expectation from the second derivative of the call surface (DTE = 2 only; unreliable on DTE ≤ 1 when premiums collapse)

If the primary (PCP) and secondary (microprice) methods diverge > 50 pts, the system flags `UNRELIABLE` and blocks all ENTER decisions.

### Module 3 — Today's Fair Value + LS Factor

Today's fair value is the theoretical price. `LS = (Expiry Fair − Spot) / Straddle` turns the gap into a volatility-normalised number.

### Module 4 — Max Pain, OI Levels, Pain Depth

Max Pain across all active strikes (those with > 0.1% of total OI — negligible-OI tails filtered). Pain depth = `2nd-lowest-pain / min-pain`:
- `> 1.5` = deep well, strong magnet
- `< 1.1` = flat surface, weak gravity

OI Corridor = active-OI filtered call resistance + put support with HHI concentration metrics (used by `strong_magnet`).

### Module 5 — Regime Classification

Six regime labels based on IG / EG / AG:

| Regime | Condition | Trading implication |
|---|---|---|
| `DOUBLE_OVERVALUED` | Spot rich vs. both anchors | Strong SHORT bias |
| `DOUBLE_UNDERVALUED` | Spot cheap vs. both anchors | Strong LONG bias |
| `INTRADAY_TRAP` | Rich vs. session but below expiry | Mean reversion likely |
| `INTRADAY_DIP` | Cheap vs. session but above expiry | Accumulation zone |
| `TREND_DAY` | AG > 80 — intraday and expiry fairs diverged | Trending session, fade carefully |
| `EQUILIBRIUM` | Near both anchors | No structural edge |

### Module 6 — Execution Setups (signals.py)

Three named setups evaluated in priority order — first match wins:

| Priority | Setup | Fires when |
|---|---|---|
| **C** | Breakout Trap | Spot breaks an OI wall but theoretical price disagrees (spot ≥ call wall + 10 with theo < call wall → SHORT trap; mirror for PUT trap) |
| **B** | Trend Momentum | Strong LS + OI wall migration (LONG: `ls > 0.35` and put support shifting up vs baseline; SHORT mirror) |
| **A** | Expiry Gravity | Strong LS + futures basis direction (LONG: `ls > 0.35` and positive basis; SHORT mirror) |
| *(fallback)* | Bullish / Bearish / Neutral tag | Reports the directional tilt from LS alone |

Stop-loss sizing uses `straddle_range × 0.5` (min 20 pts) so SL is proportional to current volatility.

### Module 7 — Options Strategy (options_strategy.py)

Merges LS + confidence + IV into the single trade card. Score/size alignment is exact:
- Strong LS + score ≥ 4 + normal IV → ATM, FULL SIZE
- Strong LS + score ≥ 4 + elevated IV → 1-OTM, REDUCED SIZE
- Strong LS + score = 3 → 1-OTM, REDUCED SIZE
- Strong LS + score = 2 → WAIT — BIAS
- Weak LS + score ≥ 3 → WATCH with target strike
- CONFLICTED / UNRELIABLE / DTE = 0 gates fire before any of the above

---

## Setup

**Prerequisites:** Python 3.11+, [Poetry](https://python-poetry.org/), Groww Trade API credentials.

```bash
# 1. Clone and install
git clone <repo>
cd Stock-trading
poetry install

# 2. Add your credentials
# Edit nifty_fair_value/config/settings.py:
#   TOTP_TOKEN  = "your-api-key"
#   TOTP_SECRET = "your-totp-secret"
#   LOT_SIZE    = 65        # current Nifty lot size
```

```bash
# 3. Run the dashboard
cd nifty_fair_value
poetry run python app/dashboard.py

# Or using the poetry script shortcut:
poetry run dashboard
```

Dashboard opens at: **http://localhost:5002**

### Running the test suite

```bash
cd nifty_fair_value
poetry run pytest tests/ -q
```

390+ tests covering every public engine function and decision branch.

---

## API Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/`                | Dashboard UI |
| `GET`  | `/api/data`        | Full current metrics snapshot (polled every 5s by the UI) |
| `GET`  | `/api/history`     | SQLite tick history for the session |
| `GET`  | `/api/expiries`    | Available option + futures expiries + current overrides |
| `POST` | `/api/expiry`      | Override the active expiry. Body: `{"opt_expiry": "YYYY-MM-DD"}` and/or `{"fut_expiry": "YYYY-MM-DD"}`. Pass `null` to clear. |

---

## API Rate Limits

| Type | Per Second | Per Minute | Usage at 5s poll |
|---|---|---|---|
| Live Data | 10 | 300 | ~24 / min (8% used) |

The poller makes 2 parallel Live Data calls per cycle (option chain + futures quote). Transient errors and HTTP 429s are retried with 1 s → 2 s → 4 s exponential backoff.

**Market-closed handling:** Outside 09:15–15:30 IST the poller detects `spot = 0` from the API, logs a single quiet `[POLLER] Outside market hours` message, switches UI status to **Market Closed**, and slows to a 60 s retry cadence — no error spam.

---

## Project Structure

```
nifty_fair_value/
├── app/
│   └── dashboard.py          Flask app + background poller (5s tick)
├── config/
│   └── settings.py           API credentials, lot size
├── data/
│   ├── groww_client.py       Parallel API fetch, retry/backoff, expiry discovery
│   ├── data_models.py        MarketData, OptionData dataclasses
│   └── persistence.py        SQLite tick history
├── engine/
│   ├── atm_selector.py       True ATM via IV-spread minimisation
│   ├── synthetic.py          OI-weighted PCP, microprice, Breeden-Litzenberger
│   ├── oi_weighted.py        Active-OI corridor + HHI concentration
│   ├── max_pain.py           Max pain strike + pain well depth
│   ├── fair_value.py         LS factor, 5-check confidence, decision point
│   ├── options_strategy.py   Trade recommendation (strike/premium/size/rationale)
│   ├── regime.py             Six-regime classification
│   ├── oi_metrics.py         pcr_oi, pcr_near_atm, pcr_at_strike, pcr_notional
│   └── signals.py            Setup A/B/C execution engine
├── templates/
│   └── fairvalue.html        Single-page trading dashboard + expiry selector
└── tests/                    390+ pytest tests
```

---

## Important Caveats

- **Decision support, not automated execution.** You confirm and place every trade manually.
- **DTE warnings are blocking.** On expiry day the strategy panel will not recommend BUY — theta decay makes long options extremely high-risk near 0 DTE.
- **Stale data banner = stop trading.** If the red banner appears, the feed has dropped. Do not act on numbers more than 60 seconds old.
- **UNRELIABLE status blocks ENTER.** If PCP and microprice diverge > 50 pts, the pricing anchor is broken and all entry signals are suppressed.
- **CONFLICTED is a hard gate.** When near-ATM PCR actively opposes LS direction, the engine refuses to enter regardless of other confirmations.

---

*For informational and systematic decision-support use only. All trading involves risk.*
