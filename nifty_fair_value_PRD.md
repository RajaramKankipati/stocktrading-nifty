# Nifty Fair Value Engine — Project Requirements Document

**Version:** 1.0  
**Data Source:** Groww Trade API (Python SDK)  
**Scope:** Theoretical Price · Today's Fair Value · Expiry Fair Value  

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Conceptual Framework](#2-conceptual-framework)
3. [Data Layer — Groww API Inputs](#3-data-layer--groww-api-inputs)
4. [Module 1 — True ATM Detection](#4-module-1--true-atm-detection)
5. [Module 2 — Theoretical Price](#5-module-2--theoretical-price)
6. [Module 3 — Today's Fair Value](#6-module-3--todays-fair-value)
7. [Module 4 — Expiry Fair Value](#7-module-4--expiry-fair-value)
8. [Module 5 — Decision Layer](#8-module-5--decision-layer)
9. [System Architecture](#9-system-architecture)
10. [Output Specification](#10-output-specification)
11. [Project Folder Structure](#11-project-folder-structure)
12. [Dependency Reference](#12-dependency-reference)

---

## 1. Project Overview

### 1.1 Goal

Build a real-time, derivatives-based fair value engine for Nifty 50 that produces
three distinct, mathematically grounded outputs:

| Output | Question Answered |
|---|---|
| **Theoretical Price** | What does no-arbitrage say Nifty *should* be, independent of any model? |
| **Today's Fair Value** | Where is the intraday equilibrium for the *current session*? |
| **Expiry Fair Value** | Where is the market being *gravitationally pulled* toward on expiry day? |

### 1.2 Design Principles

- **Model-free wherever possible.** No Black-Scholes inputs are required for core computations.
- **Hierarchy, not averaging.** Each input has a primary vs confirmatory role. Equal-weight averaging of unlike signals produces false precision.
- **Degradation-aware.** Every module detects when its inputs are stale, thin, or unreliable, and flags it rather than silently producing bad output.
- **Actionable output.** The final layer produces a regime classification and bias signal, not just numbers.

---

## 2. Conceptual Framework

### 2.1 How the Three Outputs Relate

```
Theoretical Price  ──→  model-free no-arbitrage forward (derived from full option surface)
        │
        ├── + Futures Basis  ──→  Today's Fair Value  (intraday positioning anchor)
        │   + Session VWAP
        │
        └── Expiry Fair Value  (settlement gravity)  ← independent computation from OI + Max Pain
```

The key diagnostic is not each number in isolation — it is the **gap between Today's Fair and Expiry Fair**, and how that gap evolves intraday.

### 2.2 Regimes Defined by the Two Gaps

```
Spot − Theoretical   →  intraday mispricing signal
Spot − Max Pain      →  expiry gravity signal
Theoretical − Max Pain  →  trend strength signal (are session and expiry aligned?)
```

| Intraday Gap | Expiry Gap | Regime |
|---|---|---|
| Spot > Theoretical | Spot > Max Pain | Double overvalued — both pull down |
| Spot < Theoretical | Spot < Max Pain | Double undervalued — both pull up |
| Spot > Theoretical | Spot < Max Pain | Intraday trap — session stretched, expiry pulling down |
| Spot < Theoretical | Spot > Max Pain | Intraday dip — session depressed, expiry pulling up |
| Small gap both sides | — | Equilibrium — no edge from fair value alone |

---

## 3. Data Layer — Groww API Inputs

### 3.1 Required API Calls

Every computation cycle requires two API calls:

```python
from growwapi import GrowwAPI

groww = GrowwAPI(API_AUTH_TOKEN)

# Call 1: Full option chain (primary data source)
chain = groww.get_option_chain(
    exchange=groww.EXCHANGE_NSE,
    underlying="NIFTY",
    expiry_date="YYYY-MM-DD"   # current weekly expiry
)

# Call 2: Futures quote (basis + VWAP)
fut_quote = groww.get_quote(
    exchange=groww.EXCHANGE_NSE,
    segment=groww.SEGMENT_FNO,
    trading_symbol="NIFTY25APRFUT"   # update monthly
)
```

### 3.2 Fields Used Per Module

| Field | Source | Used In |
|---|---|---|
| `underlying_ltp` | `get_option_chain` | All modules |
| `CE.ltp`, `PE.ltp` | `get_option_chain` | Theoretical Price, Straddle |
| `CE.open_interest`, `PE.open_interest` | `get_option_chain` | Theoretical Price weights, OI levels, Max Pain |
| `CE.greeks.iv`, `PE.greeks.iv` | `get_option_chain` | True ATM detection |
| `CE.greeks.delta` | `get_option_chain` | Delta-weight filter (optional) |
| `last_price` | `get_quote` (futures) | Basis computation |
| `average_price` | `get_quote` (futures) | Session VWAP proxy |
| `bid_price`, `offer_price` | `get_quote` (futures) | Microprice |
| `bid_quantity`, `offer_quantity` | `get_quote` (futures) | Microprice weights |
| `oi_day_change_percentage` | `get_quote` (futures) | Positioning pressure flag |

### 3.3 Data Quality Checks

Apply before any computation:

```python
def validate_chain(chain):
    issues = []
    spot = chain.get('underlying_ltp', 0)

    if spot <= 0:
        raise ValueError("Invalid spot price in chain response")

    strikes = chain.get('strikes', {})
    if len(strikes) < 20:
        issues.append(f"WARN: Only {len(strikes)} strikes available — chain may be incomplete")

    atm = min([int(k) for k in strikes], key=lambda x: abs(x - spot))
    atm_data = strikes.get(str(atm), {})

    ce_ltp = atm_data.get('CE', {}).get('ltp', 0)
    pe_ltp = atm_data.get('PE', {}).get('ltp', 0)

    if ce_ltp <= 0 or pe_ltp <= 0:
        issues.append("WARN: ATM CE or PE has zero LTP — stale or halted")

    return issues
```

---

## 4. Module 1 — True ATM Detection

### 4.1 Purpose

Every downstream formula requires anchoring to "ATM." The naive approach — nearest listed strike to spot — introduces a **systematic directional error** on Nifty because of its persistent downside skew (puts are structurally more expensive than calls at the same distance from spot).

The true ATM is the strike where **implied volatility is minimised** — i.e., where the put-call IV spread is closest to zero. Due to skew, this is typically 50–100 points *below* the spot on Nifty.

### 4.2 Formula

```
True_ATM = argmin_K { |IV_CE(K) − IV_PE(K)| }
```

Evaluated only over near-ATM strikes to avoid noisy far-OTM behaviour.

### 4.3 Implementation

```python
def find_true_atm(chain, window=8):
    """
    Returns the strike where CE/PE implied volatility is closest to equal.
    This is the skew-corrected ATM, not just nearest-to-spot.
    
    Parameters
    ----------
    chain  : dict    Full get_option_chain response
    window : int     Number of strikes on each side of listed ATM to consider
    
    Returns
    -------
    true_atm   : int     Strike with minimum IV spread
    iv_spread  : float   The IV spread at that strike (quality indicator)
    listed_atm : int     Nearest-to-spot strike (for comparison)
    skew_shift : int     Difference: listed_atm - true_atm (positive = skew below spot)
    """
    spot = chain['underlying_ltp']
    strikes_data = chain['strikes']
    strikes = sorted([int(k) for k in strikes_data.keys()])

    listed_atm = min(strikes, key=lambda x: abs(x - spot))
    idx = strikes.index(listed_atm)

    candidates = strikes[max(0, idx - window): idx + window + 1]

    iv_spreads = {}
    for k in candidates:
        d = strikes_data.get(str(k), {})
        ce_iv = d.get('CE', {}).get('greeks', {}).get('iv', None)
        pe_iv = d.get('PE', {}).get('greeks', {}).get('iv', None)

        # Skip if either IV is missing, zero, or anomalous
        if not ce_iv or not pe_iv or ce_iv <= 0 or pe_iv <= 0:
            continue
        if ce_iv > 100 or pe_iv > 100:   # filter anomalous prints
            continue

        iv_spreads[k] = abs(ce_iv - pe_iv)

    if not iv_spreads:
        # Fallback to listed ATM with a warning
        return listed_atm, None, listed_atm, 0

    true_atm = min(iv_spreads, key=iv_spreads.get)
    skew_shift = listed_atm - true_atm

    return true_atm, round(iv_spreads[true_atm], 4), listed_atm, skew_shift
```

### 4.4 Interpretation

| `skew_shift` | Meaning |
|---|---|
| 0 | No skew displacement — listed ATM is true ATM |
| 50–100 pts | Normal Nifty downside skew |
| > 150 pts | Elevated skew — market pricing strong downside risk |
| Negative | Unusual — call side more expensive (event/budget premium) |

### 4.5 Usage in Downstream Modules

`true_atm` replaces `listed_atm` in all subsequent computations. The `skew_shift` value itself is logged as a market condition indicator.

---

## 5. Module 2 — Theoretical Price

### 5.1 Purpose

Compute the market-implied no-arbitrage forward price of Nifty using the options surface, without any model assumptions.

Three methods are specified in ascending order of sophistication. The system computes all three and uses them for cross-validation.

---

### 5.2 Method A — OI-Weighted Synthetic Forward (Primary)

#### Principle

From put-call parity, for *any* strike K:

```
F = K + C(K) − P(K)
```

Rather than using a single ATM strike (noisy), compute this for all near-ATM strikes and weight by the minimum of CE and PE open interest at each strike.

**Why `min(CE_OI, PE_OI)` as weight?**

The liquidity of a synthetic position is constrained by its *thinner* leg. Weighting by the minimum ensures that strikes where one side is largely absent (far OTM) receive near-zero weight automatically — without any manual bandwidth selection.

#### Formula

```
F_k    = K + CE_ltp(K) − PE_ltp(K)          [per-strike synthetic forward]

w_k    = min(CE_OI(K), PE_OI(K))            [liquidity-constrained weight]

F_theo = Σ(F_k × w_k) / Σ(w_k)             [weighted average over near-ATM strikes]
```

#### Implementation

```python
def theoretical_price_pcp(chain, true_atm, window=8):
    """
    OI-weighted synthetic forward using put-call parity across near-ATM strikes.
    
    Parameters
    ----------
    chain    : dict   Full option chain response
    true_atm : int    From find_true_atm()
    window   : int    Strikes on each side of true_atm to include
    
    Returns
    -------
    theo_price   : float   Weighted theoretical price
    n_strikes    : int     Number of strikes that contributed (quality indicator)
    weighted_iv  : float   Average IV of contributing strikes (ATM IV proxy)
    """
    strikes_data = chain['strikes']
    strikes = sorted([int(k) for k in strikes_data.keys()])
    idx = strikes.index(true_atm) if true_atm in strikes else \
          min(range(len(strikes)), key=lambda i: abs(strikes[i] - true_atm))

    near_atm = strikes[max(0, idx - window): idx + window + 1]

    weighted_sum  = 0.0
    weight_total  = 0.0
    iv_weighted   = 0.0
    n_valid       = 0

    for k in near_atm:
        d      = strikes_data.get(str(k), {})
        ce     = d.get('CE', {})
        pe     = d.get('PE', {})

        ce_ltp = ce.get('ltp', 0)
        pe_ltp = pe.get('ltp', 0)
        ce_oi  = ce.get('open_interest', 0)
        pe_oi  = pe.get('open_interest', 0)
        ce_iv  = ce.get('greeks', {}).get('iv', 0)

        if ce_ltp <= 0 or pe_ltp <= 0:
            continue

        weight = min(ce_oi, pe_oi)
        if weight == 0:
            continue

        f_k = k + ce_ltp - pe_ltp

        weighted_sum += f_k * weight
        weight_total += weight
        iv_weighted  += ce_iv * weight
        n_valid      += 1

    if weight_total == 0:
        return None, 0, None

    return (
        round(weighted_sum / weight_total, 2),
        n_valid,
        round(iv_weighted / weight_total, 4)
    )
```

---

### 5.3 Method B — Futures Microprice (Real-Time Cross-Check)

#### Principle

LTP-based synthetics reflect the *last execution*. In fast markets, this can be seconds to minutes stale. The order book microprice reflects where the *next trade will happen* by weighting the best bid and ask by their opposite-side quantities.

#### Formula

```
Microprice = (Ask × Bid_Qty + Bid × Ask_Qty) / (Bid_Qty + Ask_Qty)
```

This is a volume-weighted mid price that tilts toward the side with greater resting quantity.

**Intuition:** If there are 5000 lots at the bid and 500 lots at the ask, the microprice tilts toward the ask — because strong resting buyers signal upward pressure.

#### Implementation

```python
def futures_microprice(fut_quote):
    """
    Order-book microprice of Nifty futures.
    More real-time than LTP; reflects current book pressure.
    
    Returns
    -------
    microprice    : float
    book_pressure : str     'BUY_HEAVY', 'SELL_HEAVY', or 'BALANCED'
    """
    bid   = fut_quote.get('bid_price', 0)
    ask   = fut_quote.get('offer_price', 0)
    b_qty = fut_quote.get('bid_quantity', 0)
    a_qty = fut_quote.get('offer_quantity', 0)

    if bid <= 0 or ask <= 0:
        return fut_quote.get('last_price', 0), 'NO_BOOK'

    if b_qty + a_qty == 0:
        return (bid + ask) / 2, 'BALANCED'

    microprice = (ask * b_qty + bid * a_qty) / (b_qty + a_qty)

    # Pressure: compare ratio to 50/50
    buy_ratio = b_qty / (b_qty + a_qty)
    if buy_ratio > 0.65:
        pressure = 'BUY_HEAVY'
    elif buy_ratio < 0.35:
        pressure = 'SELL_HEAVY'
    else:
        pressure = 'BALANCED'

    return round(microprice, 2), pressure
```

---

### 5.4 Method C — Breeden-Litzenberger Implied Distribution (Expiry Week)

#### Principle

This is the most complete theoretical approach. It recovers the full **risk-neutral probability distribution** of where Nifty will settle at expiry from the entire call price surface. The theoretical price is then the expected value of that distribution.

The Breeden-Litzenberger theorem states:

```
p(K) = e^(rT) · d²C/dK²
```

The risk-neutral density equals the discounted second derivative of the call price surface with respect to strike. In discrete form (Nifty strikes are spaced 50 pts):

```
p(K) ≈ e^(rT) · [C(K−ΔK) − 2·C(K) + C(K+ΔK)] / ΔK²
```

The expected value:

```
E[S_T] = Σ K · p(K) · ΔK    (summed over all valid strikes)
```

This also gives you the **distribution shape** — whether the market is pricing more downside tail than upside (which it persistently does for Nifty), quantified as:

```
Skewness_proxy = (E[S_T] − Spot) / ATM_IV
```

#### Implementation

```python
import numpy as np

def breeden_litzenberger(chain, r=0.065, T_days=None):
    """
    Recovers risk-neutral density and implied expected value from call surface.
    
    Best used on expiry week when the distribution is most concentrated and
    the strike spacing (50 pts) is fine enough relative to expected move.
    
    Parameters
    ----------
    chain  : dict    Option chain
    r      : float   Risk-free rate (approximate with repo/OIS rate)
    T_days : int     Days to expiry (required for discount factor)
    
    Returns
    -------
    expected_value   : float   Risk-neutral expected Nifty at expiry
    density          : dict    {strike: probability_mass}
    right_tail_prob  : float   Probability mass above spot (upside)
    left_tail_prob   : float   Probability mass below spot (downside)
    skew_indicator   : float   (right - left) / total — negative = downside skew
    """
    strikes_data = chain['strikes']
    spot = chain['underlying_ltp']

    if T_days is None or T_days <= 0:
        T_days = 1
    T = T_days / 365.0

    # Build call price array
    call_prices = {}
    for k_str, data in strikes_data.items():
        k = int(k_str)
        ltp = data.get('CE', {}).get('ltp', 0)
        if ltp > 0:
            call_prices[k] = ltp

    valid_strikes = sorted(call_prices.keys())
    if len(valid_strikes) < 5:
        return None, {}, None, None, None

    dK = valid_strikes[1] - valid_strikes[0]   # 50 for Nifty

    # Second derivative → density
    density = {}
    for i in range(1, len(valid_strikes) - 1):
        k    = valid_strikes[i]
        k_up = valid_strikes[i + 1]
        k_dn = valid_strikes[i - 1]

        if k_up not in call_prices or k_dn not in call_prices:
            continue

        d2C  = (call_prices[k_up] - 2 * call_prices[k] + call_prices[k_dn]) / (dK ** 2)
        prob = np.exp(r * T) * d2C * dK

        if prob > 0:    # negative = arbitrage in chain; discard
            density[k] = prob

    if not density:
        return None, {}, None, None, None

    total_mass = sum(density.values())

    # Normalise
    norm_density = {k: p / total_mass for k, p in density.items()}

    # Expected value
    expected_value = sum(k * p for k, p in norm_density.items())

    # Tail probabilities
    right_tail = sum(p for k, p in norm_density.items() if k > spot)
    left_tail  = sum(p for k, p in norm_density.items() if k < spot)
    skew_ind   = round(right_tail - left_tail, 4)

    return (
        round(expected_value, 2),
        norm_density,
        round(right_tail, 4),
        round(left_tail, 4),
        skew_ind
    )
```

---

### 5.5 Cross-Validation Logic

```python
def validate_theoretical_prices(pcp_price, microprice, bl_price=None):
    """
    Compares the three methods and flags divergence.
    Large divergence = unreliable market conditions; reduce position sizing.
    """
    prices = {'PCP': pcp_price, 'Microprice': microprice}
    if bl_price:
        prices['BL'] = bl_price

    valid = {k: v for k, v in prices.items() if v is not None}
    values = list(valid.values())

    spread = max(values) - min(values)
    mean   = sum(values) / len(values)

    status = 'RELIABLE' if spread < 20 else \
             'CAUTION'  if spread < 50 else \
             'UNRELIABLE — methods diverging, check chain quality'

    return {
        'primary'   : pcp_price,        # PCP is primary anchor
        'microprice': microprice,
        'bl_price'  : bl_price,
        'spread'    : round(spread, 2),
        'consensus' : round(mean, 2),
        'status'    : status
    }
```

---

## 6. Module 3 — Today's Fair Value

### 6.1 Purpose

The theoretical price tells you what the derivatives market implies Nifty *should* be worth. Today's fair value layers in two additional dimensions:

1. **Futures Basis** — Are futures traders paying a premium or discount to theoretical? This encodes directional positioning.
2. **Session VWAP** — Where has actual execution been concentrated today? This is the institutional average cost.

### 6.2 Basis Analysis

```
Basis = Futures_LTP − Theoretical_Price
```

| Basis | Interpretation |
|---|---|
| Strongly positive (> +30 pts) | Futures premium — bullish institutional positioning |
| Mildly positive (+10 to +30) | Normal contango — no strong signal |
| Near zero (±10) | Futures fairly priced to theoretical |
| Mildly negative (−10 to −30) | Mild discount — cautious positioning |
| Strongly negative (< −30 pts) | Futures discount — bearish institutional positioning |

Note: Nifty futures normally carry a small positive basis (cost of carry). Adjust the neutral zone interpretation to account for this:

```
Carry = Spot × r × (T/365)   # approximate expected basis
Excess_Basis = Basis − Carry  # actual positioning signal
```

### 6.3 VWAP Proxy

The Groww `get_quote` returns `average_price` on the futures contract — this is the session average trade price. While not a tick-level VWAP, it serves as a valid intraday execution anchor.

```
VWAP_Deviation = Theoretical_Price − Futures_VWAP
```

Positive deviation means the theoretical fair value is above where the market has been executing today — suggests the market has been trading *below* fair value, which is a mild bullish signal on mean-reversion logic.

### 6.4 Implementation

```python
def todays_fair_value(chain, fut_quote, true_atm, theoretical_price, r=0.065):
    """
    Computes Today's Fair Value as a layered interpretation:
    theoretical_price as anchor, basis and VWAP as directional context.
    
    Returns a structured dict suitable for display and decision logic.
    """
    spot         = chain['underlying_ltp']
    futures_ltp  = fut_quote['last_price']
    futures_vwap = fut_quote['average_price']
    oi_chg_pct   = fut_quote.get('oi_day_change_percentage', 0)

    # --- Basis ---
    basis = round(futures_ltp - theoretical_price, 2)

    # Approximate cost-of-carry adjustment (T in days required)
    # Caller should pass days_to_expiry; default to weekly estimate
    carry_approx  = round(spot * r * (7 / 365), 1)
    excess_basis  = round(basis - carry_approx, 2)

    # --- VWAP deviation ---
    vwap_dev = round(theoretical_price - futures_vwap, 2)

    # --- Spot vs theoretical ---
    spot_dev = round(spot - theoretical_price, 2)
    NOISE_THRESHOLD = 15  # points within which spot ≈ fair

    # --- ATM Straddle for range ---
    atm_data = chain['strikes'].get(str(true_atm), {})
    ce_ltp   = atm_data.get('CE', {}).get('ltp', 0)
    pe_ltp   = atm_data.get('PE', {}).get('ltp', 0)
    straddle = round(ce_ltp + pe_ltp, 1)

    intraday_upper = round(true_atm + straddle, 1)
    intraday_lower = round(true_atm - straddle, 1)

    # --- Signal generation ---
    if abs(spot_dev) <= NOISE_THRESHOLD:
        spot_signal = "AT FAIR — no mispricing edge"
    elif spot_dev > 0:
        spot_signal = f"SPOT RICH by {spot_dev:.0f} pts — short candidate"
    else:
        spot_signal = f"SPOT CHEAP by {abs(spot_dev):.0f} pts — long candidate"

    # Directional bias from basis + OI change
    if excess_basis > 15 and oi_chg_pct > 0:
        bias = "BULLISH — premium + OI build"
    elif excess_basis < -15 and oi_chg_pct > 0:
        bias = "BEARISH — discount + OI build"
    elif excess_basis > 15 and oi_chg_pct < 0:
        bias = "WEAK BULL — premium but OI unwinding"
    elif excess_basis < -15 and oi_chg_pct < 0:
        bias = "WEAK BEAR — discount but OI unwinding"
    else:
        bias = "NEUTRAL — no clear futures signal"

    return {
        'spot'             : spot,
        'theoretical_price': theoretical_price,
        'futures_ltp'      : futures_ltp,
        'futures_vwap'     : futures_vwap,
        'basis'            : basis,
        'excess_basis'     : excess_basis,
        'vwap_deviation'   : vwap_dev,
        'spot_deviation'   : spot_dev,
        'straddle_value'   : straddle,
        'intraday_upper'   : intraday_upper,
        'intraday_lower'   : intraday_lower,
        'spot_signal'      : spot_signal,
        'directional_bias' : bias,
        'oi_change_pct'    : oi_chg_pct
    }
```

---

## 7. Module 4 — Expiry Fair Value

### 7.1 Purpose

Compute the strike level toward which price is being gravitationally pulled by options market structure on expiry day.

Three independent inputs:
1. **Max Pain** — where option writer losses are minimised
2. **OI-Weighted Levels** — structural support (put OI) and resistance (call OI)
3. **Expected Move Range** — from ATM straddle value

These are *genuinely* independent: Max Pain uses OI asymmetrically (loss function), OI-weighted uses OI symmetrically (weighted centroid), and Expected Move uses premium pricing. They should not be averaged together — they serve different interpretive roles.

---

### 7.2 Max Pain

#### Formula

For each candidate settlement price T, compute total writer losses:

```
Writer_Loss(T) = Σ_K [ max(T − K, 0) × CE_OI(K) ]    ← call writers lose if T > K
               + Σ_K [ max(K − T, 0) × PE_OI(K) ]    ← put writers lose if T < K

Max_Pain = argmin_T { Writer_Loss(T) }
```

Max Pain is constrained to be one of the listed strikes (settlement occurs at a specific strike on NSE).

#### Implementation

```python
def compute_max_pain(chain):
    """
    Computes the Max Pain strike.
    Also returns the pain surface (useful for visualisation and
    understanding how steep the well is around max pain).
    
    Returns
    -------
    max_pain_strike : int
    pain_surface    : dict {strike: total_writer_loss}
    pain_depth      : float  Loss at 2nd lowest / lowest — higher = steeper well
    """
    strikes_data = chain['strikes']
    strikes = sorted([int(k) for k in strikes_data.keys()])

    pain = {}
    for target in strikes:
        total_loss = 0
        for k in strikes:
            d      = strikes_data.get(str(k), {})
            ce_oi  = d.get('CE', {}).get('open_interest', 0)
            pe_oi  = d.get('PE', {}).get('open_interest', 0)

            if target > k:
                total_loss += (target - k) * ce_oi
            if target < k:
                total_loss += (k - target) * pe_oi

        pain[target] = total_loss

    sorted_pain = sorted(pain.items(), key=lambda x: x[1])
    max_pain_strike = sorted_pain[0][0]

    # Pain depth: how steep is the well?
    # Ratio > 1.5 = steep well (strong magnet)
    # Ratio < 1.1 = flat (weak gravitational pull)
    if len(sorted_pain) >= 2:
        pain_depth = round(sorted_pain[1][1] / max(sorted_pain[0][1], 1), 3)
    else:
        pain_depth = 1.0

    return max_pain_strike, pain, pain_depth
```

---

### 7.3 OI-Weighted Levels

#### Formula

```
Call_OI_Level = Σ(K × CE_OI(K)) / Σ(CE_OI(K))    → resistance fair value
Put_OI_Level  = Σ(K × PE_OI(K)) / Σ(PE_OI(K))    → support fair value
```

These represent the *centre of gravity* of all call writing and all put writing respectively. The OI corridor [Put_Level, Call_Level] is where dealers are most exposed — they will defend these levels aggressively.

#### Implementation

```python
def oi_weighted_levels(chain):
    """
    OI-weighted centroid for call side (resistance) and put side (support).
    
    Also returns OI concentration (Herfindahl-style) to indicate whether OI
    is spread across many strikes or concentrated at a few — concentrated OI
    produces stronger gravitational levels.
    """
    strikes_data = chain['strikes']
    strikes = [int(k) for k in strikes_data.keys()]

    ce_num, ce_den = 0.0, 0.0
    pe_num, pe_den = 0.0, 0.0
    ce_sq_sum, pe_sq_sum = 0.0, 0.0

    for k in strikes:
        d = strikes_data.get(str(k), {})
        ce_oi = d.get('CE', {}).get('open_interest', 0)
        pe_oi = d.get('PE', {}).get('open_interest', 0)

        ce_num += k * ce_oi; ce_den += ce_oi
        pe_num += k * pe_oi; pe_den += pe_oi

        # For concentration measure
        ce_sq_sum += ce_oi ** 2
        pe_sq_sum += pe_oi ** 2

    call_level = round(ce_num / ce_den, 1) if ce_den > 0 else None
    put_level  = round(pe_num / pe_den, 1) if pe_den > 0 else None

    # HHI-style OI concentration (higher = more concentrated = stronger level)
    ce_hhi = round(ce_sq_sum / (ce_den ** 2), 6) if ce_den > 0 else 0
    pe_hhi = round(pe_sq_sum / (pe_den ** 2), 6) if pe_den > 0 else 0

    return {
        'call_resistance'     : call_level,
        'put_support'         : put_level,
        'oi_corridor_width'   : round(call_level - put_level, 1) if call_level and put_level else None,
        'call_oi_concentration': ce_hhi,
        'put_oi_concentration' : pe_hhi
    }
```

---

### 7.4 Expected Move (Straddle Range)

This is not a price target — it is a **range**. The ATM straddle value represents the market's 1-sigma expected move to expiry.

```
Straddle_Value = CE_ATM_LTP + PE_ATM_LTP

Expected_Upper = ATM + Straddle_Value   (≈ 84th percentile upside)
Expected_Lower = ATM − Straddle_Value   (≈ 16th percentile downside)
```

The midpoint of the straddle range should coincide with the synthetic future — if it doesn't (by more than ~20 pts), it indicates skew is pulling the centre away from ATM.

```python
def straddle_range(chain, true_atm):
    atm_data = chain['strikes'].get(str(true_atm), {})
    ce_ltp   = atm_data.get('CE', {}).get('ltp', 0)
    pe_ltp   = atm_data.get('PE', {}).get('ltp', 0)

    straddle = round(ce_ltp + pe_ltp, 1)
    upper    = round(true_atm + straddle, 1)
    lower    = round(true_atm - straddle, 1)

    # Synthetic forward from ATM
    synthetic = round(true_atm + ce_ltp - pe_ltp, 2)

    # Straddle centre vs synthetic (skew indicator)
    straddle_centre = (upper + lower) / 2  # = true_atm by construction
    skew_pull = round(synthetic - straddle_centre, 2)

    return {
        'straddle_value'  : straddle,
        'expected_upper'  : upper,
        'expected_lower'  : lower,
        'synthetic_fwd'   : synthetic,
        'skew_pull'       : skew_pull   # positive = calls dominant, negative = puts dominant
    }
```

---

### 7.5 Expiry Fair Value — Summary Output

```python
def expiry_fair_value(chain, true_atm, spot):
    """
    Combines Max Pain, OI levels, and straddle range into expiry framework.
    These three are kept separate — not averaged — because they answer
    different questions and should be read in context.
    """
    max_pain, pain_surface, pain_depth = compute_max_pain(chain)
    oi_levels   = oi_weighted_levels(chain)
    straddle    = straddle_range(chain, true_atm)

    # Gravity signal: how far is spot from max pain?
    gap_to_max_pain = round(spot - max_pain, 1)
    GRAVITY_THRESHOLD = 50  # points

    if abs(gap_to_max_pain) <= GRAVITY_THRESHOLD:
        gravity = "NEAR MAX PAIN — expiry equilibrium zone"
    elif gap_to_max_pain > 0:
        gravity = f"ABOVE MAX PAIN by {gap_to_max_pain:.0f} pts — downside gravitational pull"
    else:
        gravity = f"BELOW MAX PAIN by {abs(gap_to_max_pain):.0f} pts — upside gravitational pull"

    # Is spot within the OI corridor?
    in_corridor = (oi_levels['put_support'] <= spot <= oi_levels['call_resistance']) \
                  if oi_levels['put_support'] and oi_levels['call_resistance'] else None

    # Is spot within straddle range?
    in_range = straddle['expected_lower'] <= spot <= straddle['expected_upper']

    return {
        'max_pain'           : max_pain,
        'pain_well_depth'    : pain_depth,     # > 1.5 = strong magnet
        'call_oi_resistance' : oi_levels['call_resistance'],
        'put_oi_support'     : oi_levels['put_support'],
        'oi_corridor_width'  : oi_levels['oi_corridor_width'],
        'straddle_value'     : straddle['straddle_value'],
        'expected_upper'     : straddle['expected_upper'],
        'expected_lower'     : straddle['expected_lower'],
        'synthetic_fwd'      : straddle['synthetic_fwd'],
        'skew_pull'          : straddle['skew_pull'],
        'gap_to_max_pain'    : gap_to_max_pain,
        'gravity_signal'     : gravity,
        'spot_in_oi_corridor': in_corridor,
        'spot_in_straddle'   : in_range
    }
```

---

## 8. Module 5 — Decision Layer

### 8.1 Purpose

Synthesise all outputs into a single regime classification and actionable bias signal. The decision layer does not generate trade signals directly — it generates the *context* within which your existing entry rules operate.

### 8.2 The Three Gaps

```
Intraday_Gap  = Spot − Theoretical_Price     (session mispricing)
Expiry_Gap    = Spot − Max_Pain              (expiry gravity)
Alignment_Gap = Theoretical_Price − Max_Pain (are session and expiry aligned?)
```

### 8.3 Regime Classification

```python
def classify_regime(today_fv, expiry_fv, theoretical_price):
    """
    Classifies the current market regime from the three-gap framework.
    
    Returns a regime label, bias direction, and confidence level.
    """
    spot        = today_fv['spot']
    max_pain    = expiry_fv['max_pain']
    intraday_g  = round(spot - theoretical_price, 1)
    expiry_g    = round(spot - max_pain, 1)
    align_g     = round(theoretical_price - max_pain, 1)

    # Thresholds
    INTRADAY_THRESH = 20   # pts to declare intraday mispricing
    EXPIRY_THRESH   = 50   # pts to declare expiry gravity
    ALIGN_THRESH    = 80   # pts to declare divergence between session and expiry

    # Regime logic
    intraday_rich  = intraday_g >  INTRADAY_THRESH
    intraday_cheap = intraday_g < -INTRADAY_THRESH
    expiry_above   = expiry_g   >  EXPIRY_THRESH
    expiry_below   = expiry_g   < -EXPIRY_THRESH
    fairs_diverged = abs(align_g) > ALIGN_THRESH

    if intraday_rich and expiry_above:
        regime    = "DOUBLE_OVERVALUED"
        bias      = "SHORT"
        rationale = "Both intraday and expiry fair values below spot — strong downside pull"
        confidence = "HIGH" if today_fv['directional_bias'].startswith("BEAR") else "MEDIUM"

    elif intraday_cheap and expiry_below:
        regime    = "DOUBLE_UNDERVALUED"
        bias      = "LONG"
        rationale = "Both intraday and expiry fair values above spot — strong upside pull"
        confidence = "HIGH" if today_fv['directional_bias'].startswith("BULL") else "MEDIUM"

    elif intraday_rich and expiry_below:
        regime    = "INTRADAY_TRAP"
        bias      = "SHORT"
        rationale = "Spot stretched above intraday fair; expiry pull is upward — likely mean reversion, not trend"
        confidence = "MEDIUM"

    elif intraday_cheap and expiry_above:
        regime    = "INTRADAY_DIP"
        bias      = "LONG"
        rationale = "Spot depressed below intraday fair; expiry pull is upward — accumulation zone"
        confidence = "MEDIUM"

    elif fairs_diverged:
        regime    = "TREND_DAY"
        bias      = "WITH_TREND" if align_g > 0 else "COUNTER_AVAILABLE"
        rationale = f"Intraday and expiry fairs diverged by {align_g:.0f} pts — trending day underway"
        confidence = "MEDIUM"

    else:
        regime    = "EQUILIBRIUM"
        bias      = "NEUTRAL"
        rationale = "Spot near both fair values — no structural edge from derivatives alone"
        confidence = "LOW"

    return {
        'regime'          : regime,
        'bias'            : bias,
        'rationale'       : rationale,
        'confidence'      : confidence,
        'intraday_gap'    : intraday_g,
        'expiry_gap'      : expiry_g,
        'alignment_gap'   : align_g,
        'pain_well_depth' : expiry_fv['pain_well_depth']
    }
```

---

## 9. System Architecture

### 9.1 Execution Flow

```
┌─────────────────────────────────────────────────────────┐
│                    Input Layer                          │
│   get_option_chain()  +  get_quote(futures)             │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│              Module 1: True ATM Detection               │
│   IV spread minimisation → true_atm, skew_shift         │
└────────────────────┬────────────────────────────────────┘
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
┌──────────────────┐   ┌────────────────────────────────┐
│   Module 2:      │   │        Module 4:               │
│  Theoretical     │   │    Expiry Fair Value           │
│    Price         │   │  Max Pain + OI Levels +        │
│  PCP + Microprice│   │  Straddle Range                │
│  + B-L (expiry)  │   │                                │
└────────┬─────────┘   └────────────────┬───────────────┘
         │                              │
         ▼                              │
┌──────────────────────────┐            │
│      Module 3:           │            │
│   Today's Fair Value     │            │
│  Theoretical + Basis     │            │
│  + VWAP + Spot Signal    │            │
└────────────┬─────────────┘            │
             │                          │
             └────────────┬─────────────┘
                          ▼
┌─────────────────────────────────────────────────────────┐
│               Module 5: Decision Layer                  │
│   Three-gap regime classification + bias signal         │
└─────────────────────────────────────────────────────────┘
```

### 9.2 Refresh Cadence

| Module | Suggested Refresh |
|---|---|
| True ATM detection | Every 5 minutes |
| Theoretical Price (PCP) | Every 2 minutes |
| Microprice | Every 30 seconds |
| Today's Fair Value | Every 2 minutes |
| Expiry Fair Value | Every 5 minutes |
| Decision Layer | Every 2 minutes |
| Breeden-Litzenberger | Every 15 minutes (expiry week only) |

---

## 10. Output Specification

### 10.1 Terminal Display Format

```python
def display_output(atm_result, theo_result, today_result, expiry_result, regime_result):
    true_atm, iv_spread, listed_atm, skew_shift = atm_result
    theoretical_price, n_strikes, atm_iv = theo_result

    print("\n" + "="*60)
    print("  NIFTY FAIR VALUE ENGINE")
    print("="*60)

    print(f"\n  Spot              : {today_result['spot']:.1f}")
    print(f"  Listed ATM        : {listed_atm}")
    print(f"  True ATM          : {true_atm}  (skew shift: {skew_shift:+d} pts)")
    print(f"  ATM IV            : {atm_iv:.2f}%")

    print(f"\n── THEORETICAL PRICE ──────────────────────────────────")
    print(f"  PCP Weighted      : {theoretical_price:.2f}  ({n_strikes} strikes)")
    print(f"  Futures Microprice: {today_result['futures_ltp']:.2f}")
    print(f"  Basis             : {today_result['basis']:+.1f}  (excess: {today_result['excess_basis']:+.1f})")

    print(f"\n── TODAY'S FAIR VALUE ──────────────────────────────────")
    print(f"  Theoretical Price : {theoretical_price:.2f}")
    print(f"  Session VWAP      : {today_result['futures_vwap']:.2f}")
    print(f"  VWAP Deviation    : {today_result['vwap_deviation']:+.1f}")
    print(f"  Intraday Range    : {today_result['intraday_lower']:.0f} – {today_result['intraday_upper']:.0f}")
    print(f"  → {today_result['spot_signal']}")
    print(f"  → {today_result['directional_bias']}")

    print(f"\n── EXPIRY FAIR VALUE ───────────────────────────────────")
    print(f"  Max Pain          : {expiry_result['max_pain']}  (well depth: {expiry_result['pain_well_depth']:.2f})")
    print(f"  OI Corridor       : {expiry_result['put_oi_support']:.0f} – {expiry_result['call_oi_resistance']:.0f}")
    print(f"  Straddle Range    : {expiry_result['expected_lower']:.0f} – {expiry_result['expected_upper']:.0f}")
    print(f"  Straddle Value    : {expiry_result['straddle_value']:.1f}")
    print(f"  → {expiry_result['gravity_signal']}")

    print(f"\n── REGIME ──────────────────────────────────────────────")
    print(f"  Regime            : {regime_result['regime']}")
    print(f"  Bias              : {regime_result['bias']}")
    print(f"  Confidence        : {regime_result['confidence']}")
    print(f"  Intraday Gap      : {regime_result['intraday_gap']:+.1f} pts")
    print(f"  Expiry Gap        : {regime_result['expiry_gap']:+.1f} pts")
    print(f"  Alignment Gap     : {regime_result['alignment_gap']:+.1f} pts")
    print(f"\n  {regime_result['rationale']}")
    print("="*60 + "\n")
```

### 10.2 Structured Dict Output (for logging / downstream use)

```python
def full_output_dict(atm_result, theo_result, today_result, expiry_result, regime_result):
    return {
        'timestamp'       : pd.Timestamp.now().isoformat(),
        'atm'             : {'true': atm_result[0], 'listed': atm_result[2], 'skew_shift': atm_result[3]},
        'theoretical'     : {'price': theo_result[0], 'n_strikes': theo_result[1], 'atm_iv': theo_result[2]},
        'today_fv'        : today_result,
        'expiry_fv'       : expiry_result,
        'regime'          : regime_result
    }
```

---

## 11. Project Folder Structure

```
nifty_fair_value/
│
├── main.py                    # Entry point — runs computation loop
│
├── config.py                  # API token, expiry date, futures symbol, thresholds
│
├── data/
│   └── fetcher.py             # Groww API calls + data validation
│
├── modules/
│   ├── atm_detection.py       # Module 1: True ATM via IV spread
│   ├── theoretical_price.py   # Module 2: PCP + Microprice + B-L
│   ├── todays_fair.py         # Module 3: Basis + VWAP + range
│   ├── expiry_fair.py         # Module 4: Max Pain + OI + Straddle
│   └── decision.py            # Module 5: Regime + signal
│
├── output/
│   ├── display.py             # Terminal formatted output
│   └── logger.py              # CSV/JSON logging for review
│
└── requirements.txt
```

---

## 12. Dependency Reference

```
growwapi           # Groww Python SDK
numpy              # Breeden-Litzenberger density computation
pandas             # Logging and time handling
```

```python
# config.py template
API_AUTH_TOKEN    = "your_token_here"
EXPIRY_DATE       = "2025-04-24"        # update weekly
FUTURES_SYMBOL    = "NIFTY25APRFUT"     # update monthly
RISK_FREE_RATE    = 0.065               # approximate repo rate
DAYS_TO_EXPIRY    = 5                   # update daily
NOISE_THRESHOLD   = 15                  # pts — spot within this of theo = "at fair"
GRAVITY_THRESHOLD = 50                  # pts — spot within this of max pain = equilibrium
PCP_WINDOW        = 8                   # strikes on each side of true ATM for PCP
ATM_WINDOW        = 8                   # strikes on each side for IV spread detection
```

---

*End of Project Requirements Document*
