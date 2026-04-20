# Nifty Options Dashboard

A real-time derivatives engine that reads the Groww Trade API every 5 seconds and converts raw market data into a single actionable trade decision — which Nifty 50 option to buy, at what strike, for what premium, and with what maximum loss.

---

## What It Does

The dashboard answers one question on every refresh:

> **Should I buy a CE, buy a PE, sell a straddle, or stay out?**

If the signal is strong and all checks agree it shows:

```
Strategy:  BUY CE — ATM
Strike:    24500
Premium:   ₹ 142.50
Max Loss:  ₹ 9,262 / lot

LONG gravity (LS +0.4120) | 4/5 signals confirm
```

If conditions are not met it shows **NO TRADE** and explains exactly why.

---

## Dashboard Walkthrough

The dashboard has five sections from top to bottom. Here is what each one shows and how to read it.

---

### Section 1 — Options Strategy *(the only panel you act on)*

This is the trade recommendation. Everything else on the dashboard feeds into this.

| Field | What it means |
|---|---|
| **Strategy** | `BUY CE — ATM`, `BUY PE — 1-OTM`, `SELL STRADDLE — ATM`, or `NO TRADE` |
| **Strike** | The specific Nifty strike to trade |
| **Premium** | Current LTP of that option — what you pay per unit |
| **Max Loss / Lot** | Premium × lot size (65 units). The most you can lose on one lot if the option expires worthless |
| **Size Note** | `FULL SIZE` / `REDUCED SIZE` / `SMALL SIZE` — how much of your normal position to take |
| **Rationale** | One-line explanation: direction, confidence score, IV level |
| **IV Regime** | `LOW` / `NORMAL` / `ELEVATED` / `HIGH` — how expensive options are right now |
| **DTE Warning** | Red banner on expiry day or one day before. Theta destroys buyers near expiry. |

**Color code:**
- Green card = BUY signal (CE or PE)
- Amber card = SELL premium (straddle)
- Grey card = no trade

**Decision rules in plain English:**

| Situation | Output |
|---|---|
| Strong gravity (`\|LS\| > 0.35`) + 4–5 checks pass + IV normal | BUY CE/PE — ATM, FULL SIZE |
| Strong gravity + IV elevated (options expensive) | BUY CE/PE — 1 strike OTM, REDUCED SIZE |
| Moderate gravity (`\|LS\| > 0.35`) + only 3 checks pass | BUY CE/PE — 1-OTM, REDUCED SIZE |
| No direction + IV elevated + 2 days or more left | SELL STRADDLE — ATM (collect premium) |
| Expiry day (DTE = 0) | NO TRADE (theta risk too high for buyers) |
| Only 1 check passes | NO TRADE (setup not confirmed) |
| Theoretical price flagged UNRELIABLE | NO TRADE (pricing anchor broken) |

**IV thresholds:**
- `< 12%` → LOW — cheap to buy options
- `12–18%` → NORMAL — standard conditions
- `18–25%` → ELEVATED — prefer OTM strikes
- `> 25%` → HIGH — consider selling premium instead

---

### Section 2 — LS Signal

The LS Factor is the primary directional indicator. It measures how far the expiry anchor is from current spot, scaled to the current volatility of the options market.

```
LS = (Expiry Fair − Spot) / Straddle
```

- **Expiry Fair** = where the market is gravitating toward by options expiry settlement. Computed as `Max Pain × 0.6 + OI Midpoint × 0.4`.
- **Straddle** = ATM call + put premium. Scales the signal to current volatility — a 100-point gap means more at low IV than at high IV.

**How to read the LS value:**

| LS Value | Direction | Meaning |
|---|---|---|
| `> +0.35` | LONG | Strong upside gravitational pull toward expiry |
| `+0.15 to +0.35` | WEAK LONG | Mild upside bias, not enough alone |
| `−0.15 to +0.15` | FLAT | No directional gravity |
| `−0.15 to −0.35` | WEAK SHORT | Mild downside bias |
| `< −0.35` | SHORT | Strong downside gravitational pull |

Below the LS value you see the **Decision** card (ENTER / WAIT / SKIP / NO TRADE) and the **Confidence panel** (0–5 score).

#### Confidence Score (0–5 checks)

Five independent checks confirm or deny the LS direction. A score of 4 or 5 is required for full-size entry.

| Check | What it looks at | Passes when… |
|---|---|---|
| **Futures bias aligned** | Futures basis vs. carry + open interest change | Excess basis and OI build both agree with LS direction |
| **Strong pain magnet** | How deep the max pain well is (`pain_depth > 1.5`) | Settlement gravity is strong — option writers heavily concentrated |
| **Intraday gap aligned** | Spot vs. theoretical price (IG) | Spot is cheap (for LONG) or rich (for SHORT) vs. fair value |
| **Spot inside OI corridor** | Whether spot is between put support and call resistance | Spot is inside the active dealer zone |
| **PCR aligned** | Put-call ratio by open interest | PCR > 1.1 for LONG (put heavy = market expects support), PCR < 0.9 for SHORT |

**Decision logic:**
- 4–5 checks + IG aligned → **ENTER** (execute the trade)
- 4–5 checks + IG not aligned yet → **WAIT** (structure is there, wait for the intraday gap to confirm)
- 3 checks + IG aligned → **ENTER REDUCED SIZE**
- ≤ 2 checks → **SKIP** (not enough confirmation)

---

### Section 3 — Session Gaps

Three gap values quantify exactly where the market stands right now relative to its anchors.

| Gap | Formula | What it tells you |
|---|---|---|
| **IG — Intraday Gap** | `Spot − Theoretical Price` | Is spot cheap or rich vs. its fair value *today*? Negative = cheap = potential upside. |
| **EG — Expiry Gap** | `Spot − Max Pain` | How far is spot from where option writers need price to settle? Positive = above pain = gravitational pull down. |
| **AG — Alignment Gap** | `Theoretical Price − Max Pain` | Are today's fair value and expiry anchor moving together (< 80pts) or diverging (trending day)? |

**Interpreting the gaps:**
- IG > +20 pts: spot is expensive, mean-reversion risk
- IG < −20 pts: spot is cheap, upside pull likely
- EG > +50 pts: well above max pain, settlement gravity pulling down
- EG < −50 pts: well below max pain, settlement gravity pulling up
- AG > +80 pts: trending session — intraday and expiry fairs have separated

---

### Section 4 — Key Levels

Six compact numbers summarising the structural market state.

| Card | What it shows |
|---|---|
| **Spot** | Current Nifty index price and whether it is at fair, rich, or cheap vs. theoretical price |
| **True ATM** | Strike with the tightest CE/PE IV spread — where the market's 50/50 point actually is (often 50–100 pts below spot due to Nifty's structural downside skew) |
| **Max Pain** | Strike where total option writer losses are minimised — the strongest expiry gravitational anchor |
| **Straddle Range** | `Lower – Upper` bounds for the current expiry (±straddle from ATM). If spot stays inside this range, most options expire worthless. |
| **OI Corridor** | `Put Support – Call Resistance` from the dominant OI strikes. The zone where dealers are most active. |
| **PCR (OI)** | Put-to-call ratio by open interest. > 1.2 = put heavy (bullish support), < 0.8 = call heavy (bearish resistance). |

---

### Section 5 — Visual Option Chain (ATM ±3)

The seven strikes nearest to the true ATM, shown with:

- **OI bars** — horizontal bars proportional to open interest. Long red bars on the call side = strong call writing (resistance). Long cyan bars on the put side = strong put writing (support).
- **Call LTP / Put LTP** — current last traded prices
- **Strike** — ATM strike is highlighted in white/cyan
- ITM cells have a subtle background shade (calls below spot, puts above spot)

Use this table to visually confirm which strikes have the most writing activity and where the real support/resistance is.

---

## Header Bar

At the top of the page:

| Element | Meaning |
|---|---|
| **Green dot / CONNECTED** | Live data feed active, refreshing every 5 seconds |
| **Amber dot** | Initializing or authenticating |
| **Red dot / RECONNECTING** | API error — last data may be stale |
| **OPT EXP** | Active options expiry date and days remaining (DTE) |
| **FUT EXP** | Active Nifty futures expiry date |
| **LIVE · HH:MM:SS** | Timestamp of last successful data refresh |

A red banner appears at the top of the page if data is stale (no successful refresh in > 60 seconds) — **do not trade from stale data**.

---

## Engine — How the Numbers Are Computed

### Module 1 — True ATM Detection

The dashboard does not use the nearest 50-pt strike as ATM. It finds the strike where the CE and PE implied volatilities are closest to each other (minimum IV spread). This is the market's real pricing centre. On Nifty this is typically 50–100 pts below spot due to a structural put skew from institutional hedging.

Guards in place:
- If Groww returns identical CE/PE IVs for all strikes (weekend/closed market), falls back to listed ATM.
- True ATM is capped within 3 strikes (150 pts) of listed ATM — larger deviations indicate data quality issues, not genuine skew.

### Module 2 — Theoretical Price (OI-Weighted PCP)

The fair value of Nifty for the current session, computed via put-call parity across the 8 strikes nearest ATM, weighted by `min(CE_OI, PE_OI)`. Strikes with low open interest get near-zero weight automatically. Validated against a futures microprice (order-book volume-weighted mid). If the two methods diverge > 50 pts, the system flags `UNRELIABLE` and blocks all ENTER decisions.

### Module 3 — Expiry Fair Value and LS Factor

The expiry anchor is: `Max Pain × 0.6 + OI Midpoint × 0.4`

Max Pain is where option writer losses are minimised — the strongest settlement gravitational pull. The OI midpoint adds the structural support/resistance layer (where dealers are most active).

The LS Factor divides this gap by the current straddle premium, making the thresholds (±0.35) scale-invariant across different Nifty levels and IV regimes.

### Module 4 — Max Pain and Pain Well Depth

Max Pain is calculated across all active strikes (those with > 0.1% of total OI). The Pain Well Depth measures how steeply the pain surface drops toward max pain:

- `pain_depth > 1.5` = strong magnet (next-cheapest strike costs 50%+ more than max pain)
- `pain_depth < 1.1` = flat surface, weak gravitational pull

### Module 5 — Regime Classification

Three regime labels based on the gap values:

| Regime | Condition | Trading implication |
|---|---|---|
| DOUBLE_OVERVALUED | Spot rich vs. both anchors | Strong SHORT bias |
| DOUBLE_UNDERVALUED | Spot cheap vs. both anchors | Strong LONG bias |
| INTRADAY_TRAP | Rich vs. session but below expiry | Mean reversion likely, not trend |
| INTRADAY_DIP | Cheap vs. session but above expiry | Accumulation zone |
| TREND_DAY | Intraday and expiry fairs diverged | Trending session, fade carefully |
| EQUILIBRIUM | Near both anchors | No structural edge |

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

---

## API Rate Limits

| Type | Per Second | Per Minute | Usage at 5s poll |
|---|---|---|---|
| Live Data | 10 | 300 | ~24 / min (8% used) |

The poller makes 2 parallel Live Data calls per cycle (option chain + futures quote). Transient errors and HTTP 429s are retried with 1s → 2s → 4s exponential backoff.

---

## Project Structure

```
nifty_fair_value/
├── app/
│   └── dashboard.py          Flask app + background poller (refreshes every 5s)
├── config/
│   └── settings.py           API credentials, lot size
├── data/
│   ├── groww_client.py        Parallel API fetch with retry/backoff
│   ├── data_models.py         MarketData, OptionData dataclasses
│   └── persistence.py         SQLite tick history (auto-cleaned each day)
├── engine/
│   ├── atm_selector.py        True ATM via IV-spread minimisation
│   ├── synthetic.py           OI-weighted PCP, microprice, Breeden-Litzenberger
│   ├── oi_weighted.py         Active-OI filtered corridor (call resistance / put support)
│   ├── max_pain.py            Max pain strike + pain well depth
│   ├── fair_value.py          LS factor, confidence, decision point, expiry fair
│   ├── options_strategy.py    Trade recommendation (strike, premium, size, rationale)
│   ├── regime.py              Three-gap regime classification
│   ├── oi_metrics.py          Put-call ratio
│   └── signals.py             Legacy execution setups
└── templates/
    └── fairvalue.html         Single-page trading dashboard
```

---

## Important Caveats

- **This tool is for decision support, not automated execution.** You confirm and place every trade manually.
- **Always check the DTE warning.** On expiry day (DTE = 0) the strategy panel will block BUY decisions — theta decay makes buying options extremely high-risk.
- **Stale data banner = stop trading.** If the red banner appears at the top, the feed has dropped. Do not act on numbers more than 60 seconds old in a live market.
- **UNRELIABLE status blocks ENTER.** If the theoretical price and microprice diverge by more than 50 pts, the system cannot reliably price options and suppresses all entry signals.

---

*For informational and systematic decision-support use only. All trading involves risk.*
