# STOP_MARKET Trade Recording — Build Specification

## Objective

Fix STOP_MARKET trade recording reliability WITHOUT breaking:
- strategy logic
- execution flow
- risk calculations

Ensure:
- Trade history is ALWAYS correct
- No false trade closures
- No corrupted exit_price or R-multiple

---

## CRITICAL RULES (MUST FOLLOW)

1. NEVER guess exit_price
2. NEVER use unrelated trades as exit
3. NEVER trigger `on_position_closed()` unless closure is 100% confirmed
4. DO NOT change strategy / regime / signal logic
5. MINIMIZE code changes

---

## REQUIRED FIXES

### 1. User Stream — robust reduceOnly detection

**File:** `binance_user_stream.py`

Replace:
```python
if not o.get("R"):
    return None
```

With:
```python
reduce_only = o.get("R") or o.get("r")
if not reduce_only:
    return None
```

---

### 2. Relax client ID filter SAFELY

**Logic:**
```python
cid = str(o.get("c", ""))
symbol_in_msg = str(o.get("s", ""))
is_engine_order = cid.startswith("bfat_")

if not is_engine_order:
    if expected_symbol is None or symbol_in_msg != expected_symbol:
        return None
```

- DO NOT remove filtering entirely
- Only allow non-bfat orders if symbol matches

---

### 3. Add STOP FILLED logging (before on_position_closed)

```python
logger.info(
    "[STOP_FILLED_DETECTED]",
    extra={"symbol": symbol_in_msg, "price": exit_price}
)
```

---

### 4. Stop order validation (engine.py)

After placing STOP_MARKET (entry and trailing):

```python
if not stop_resp or "orderId" not in stop_resp:
    raise RuntimeError("STOP order placement failed")

logger.info(
    "[STOP_ORDER_PLACED]",
    extra={"orderId": stop_resp.get("orderId"), "stopPrice": stop_price}
)
```

Apply to:
- entry stop
- trailing stop

---

### 5. Fallback detection (CRITICAL)

**File:** `engine.py`  
**Location:** VERY START of `on_candle_close` (after kill switch)

#### 5-1. Detect closure safely

```python
if self._state_machine.state == PositionState.OPEN:
    pos = self._state_machine.position
    if pos is not None:
        try:
            live = self._execution.get_position(self._symbol)
            amt_raw = live.get("positionAmt") or live.get("position_amt") or 0
            amt = float(amt_raw)
            # Use tolerance (IMPORTANT)
            if abs(amt) < 1e-8:
                # First detection → mark pending and wait for next confirmation
                if not self._pending_fallback_close:
                    self._pending_fallback_close = True
                    return
            else:
                # Position still open → clear pending flag
                self._pending_fallback_close = False
```

#### 5-2. Filter trades AFTER position entry (MANDATORY)

Convert position entry time to timestamp:

```python
from datetime import datetime

entry_ts = int(datetime.fromisoformat(pos.entry_time.replace("Z", "")).timestamp() * 1000)
```

#### 5-3. Use ONLY opposite-side trades (DO NOT MODIFY)

```python
if pos.side == Side.LONG:
    is_exit = not is_buyer  # SELL
else:
    is_exit = is_buyer      # BUY
```

#### 5-4. Prevent false fallback closure (MANDATORY)

**Condition A — must have valid exit_price:**
```python
if exit_price is None:
    return
```

**Condition B — require recent trade:**
```python
if len(trades) == 0:
    return
```

**Condition C — require trade AFTER entry:**

Ensure at least one trade passes BOTH time filter AND side filter. If none → DO NOT CLOSE.

#### 5-5. Trade loop with time filtering and optional weighted exit

```python
trades = self._execution.get_user_trades(self._symbol, limit=10)

if len(trades) == 0:
    return

entry_ts = int(datetime.fromisoformat(pos.entry_time.replace("Z", "")).timestamp() * 1000)

total_qty = 0.0
total_value = 0.0

for t in trades:
    trade_time = int(t.get("time", 0))
    # CRITICAL: ignore trades before entry
    if trade_time < entry_ts:
        continue

    qty = float(t.get("qty", 0))
    price = float(t.get("price", 0))
    is_buyer = t.get("buyer")

    if pos.side == Side.LONG:
        is_exit = not is_buyer
    else:
        is_exit = is_buyer

    if qty > 0 and is_exit:
        total_qty += qty
        total_value += qty * price

if total_qty > 0:
    exit_price = total_value / total_qty
else:
    exit_price = None
```

#### 5-6. HARD SAFETY CHECK

```python
if exit_price is None:
    # DO NOT CLOSE — insufficient confirmation
    return
```

#### 5-7. Final fallback close

```python
self._system_log.insert(
    level="WARNING",
    event="position_closed_fallback",
    message=f"Fallback close detected. Exit: {exit_price}"
)

self._pending_fallback_close = False
self.on_position_closed(exit_price, equity)
return
```

---

### 6. Pending Fallback Confirmation (FINAL SAFETY PATCH)

**File:** `engine.py`  
**Class:** `BFATEngine`

**Objective:** Eliminate false positives from temporary `positionAmt = 0` by requiring confirmation across consecutive checks.

#### 6-1. Add instance variable in `__init__`

```python
self._pending_fallback_close = False
```

#### 6-2. Modify amt check (inside existing fallback block)

Replace `if abs(amt) < 1e-8:` block with:
- First detection → set `_pending_fallback_close = True` → `return` (no close)
- Else (position still open) → `_pending_fallback_close = False`

#### 6-3. Reset flag before on_position_closed

Add `self._pending_fallback_close = False` immediately before `self.on_position_closed(exit_price, equity)`.

#### 6-4. Reset flag when position still exists

Add `else:` branch after amt check: when `abs(amt) >= 1e-8`, set `_pending_fallback_close = False`.

**Expected behavior:**
| Case | Scenario | Result |
|------|----------|--------|
| 1 | Real stop hit (WS missed) | Candle 1: amt=0 → pending=True → NO close; Candle 2: amt=0 → confirmed → close |
| 2 | Temporary API glitch | Candle 1: amt=0 → pending=True; Candle 2: amt≠0 → reset → NO close |
| 3 | Normal (WS works) | Fallback not used → no change |

**STRICTLY FORBIDDEN:**
- Removing existing safety checks
- Changing exit_price logic
- Adding new APIs or dependencies
- Refactoring fallback structure

---

### 7. Fallback Safety Patch (STRICT IMPROVEMENT)

**CRITICAL RULES:**
1. NEVER use trades BEFORE position entry
2. NEVER assume first trade is exit
3. NEVER close position without strong confirmation
4. DO NOT change strategy / regime / trailing logic
5. MINIMIZE code changes

**Required improvements:**
- Filter trades after position entry (time-based)
- Use ONLY opposite-side trades (already exists)
- Condition A: valid exit_price
- Condition B: require recent trade (`len(trades) > 0`)
- Condition C: require trade AFTER entry (time + side filter)
- (Optional) Weighted exit price for partial fills: `exit_price = total_value / total_qty`

**Expected behavior:**
| Case | Scenario | Result |
|------|----------|--------|
| 1 | Correct fallback | position closed on exchange → correct exit_price (post-entry trades only) |
| 2 | Old trades exist | ignored (time filter) |
| 3 | No valid exit trades | NO closure (safe) |
| 4 | Partial fills | weighted exit_price (accurate) |

**STRICTLY FORBIDDEN:**
- Using trades without time filtering
- Using first trade blindly
- Closing without side verification
- Removing existing safety checks

---

### 8. DO NOT modify (any patch)

- `_trailing_logic()`
- StrategyEngine
- execution client (except already added get_user_trades)
- signal logic
- risk logic

---

## Expected Behavior

| Case | Scenario | Result |
|------|----------|--------|
| 1 | Normal SL hit | User Stream detects → Trade recorded |
| 2 | WS missed | Fallback detects position=0 → Correct exit_price → Trade recorded |
| 3 | Noise / mismatch | NO false close |

---

## STRICTLY FORBIDDEN

- Using first trade blindly
- Using trades without time filtering
- Removing reduceOnly condition
- Removing symbol check
- Closing without side verification
- Calling on_position_closed without confirmation
- Removing existing safety checks

---

## Additional Requirements

- **BinanceExecutionClient:** Add `get_user_trades(symbol, limit)` for fallback exit price lookup.
- Focus on correctness and safety.
