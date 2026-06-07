# Patch sizing.py and runner.py for alt_only full-equity single position
import os
os.chdir(os.path.join(os.path.dirname(__file__), "backend"))

# 1. sizing.py - add parameter
path = "app/engine/sizing.py"
with open(path, "r", encoding="utf-8") as f:
    s = f.read()

# Add single_position_full_equity param
if "single_position_full_equity: bool = False" not in s:
    s = s.replace(
        "atr_map: dict[str, float] | None = None,\n) -> SizingResult:",
        "atr_map: dict[str, float] | None = None,\n    single_position_full_equity: bool = False,\n) -> SizingResult:",
        1,
    )

# Step 2: force top_k=1 when single_position_full_equity
old2 = "    # Step 2: Dynamic Top-K selection\n    effective_top_k = top_k\n    if top_k_enabled:\n        effective_top_k = _compute_dynamic_top_k(raw_weights, equity, top_k)\n        \n    if top_k_enabled and len(raw_weights) > effective_top_k:"
new2 = "    # Step 2: Dynamic Top-K selection (alt mode: one position, full equity)\n    effective_top_k = 1 if single_position_full_equity else top_k\n    if top_k_enabled and not single_position_full_equity:\n        effective_top_k = _compute_dynamic_top_k(raw_weights, equity, top_k)\n\n    if top_k_enabled and len(raw_weights) > effective_top_k:"
if new2 not in s and old2 in s:
    s = s.replace(old2, new2, 1)

# Step 4: gross_notional - when single_position_full_equity use 95% of max
old4 = "    # Step 4: gross_notional from vol targeting\n    # gross_notional = equity x target_vol / avg_weighted_vol\n    avg_vol = 0.0\n    for sym, w in normalised.items():\n        sv = vol_map.get(sym, target_vol)\n        avg_vol += abs(w) * sv\n    if avg_vol <= 0:\n        avg_vol = target_vol\n\n    gross_notional = equity * target_vol / avg_vol\n    # Cap by leverage target\n    max_notional = equity * leverage_target\n    if gross_notional > max_notional:\n        gross_notional = max_notional\n\n    result.gross_notional = gross_notional"
# Use exact chars - × might be different
old4_alt = "    gross_notional = equity * target_vol / avg_vol\n    # Cap by leverage target\n    max_notional = equity * leverage_target\n    if gross_notional > max_notional:\n        gross_notional = max_notional\n\n    result.gross_notional = gross_notional"
new4 = "    # Step 4: gross_notional from vol targeting (or full equity when single-position mode)\n    max_notional = equity * leverage_target\n    if single_position_full_equity and len(normalised) == 1:\n        gross_notional = max_notional * 0.95  # 95% of buying power for the single position\n    else:\n        avg_vol = 0.0\n        for sym, w in normalised.items():\n            sv = vol_map.get(sym, target_vol)\n            avg_vol += abs(w) * sv\n        if avg_vol <= 0:\n            avg_vol = target_vol\n        gross_notional = equity * target_vol / avg_vol\n        if gross_notional > max_notional:\n            gross_notional = max_notional\n\n    result.gross_notional = gross_notional"
if new4 not in s:
    if "max_notional = equity * leverage_target" in s and "result.gross_notional = gross_notional" in s:
        # Find and replace the block
        start = s.find("    # Step 4: gross_notional")
        if start == -1:
            start = s.find("    avg_vol = 0.0\n    for sym, w in normalised.items():")
        end = s.find("    result.gross_notional = gross_notional", start) + len("    result.gross_notional = gross_notional")
        if start != -1 and end > start:
            s = s[:start] + new4 + s[end:]

# Step 5: single position full equity notional path
old5 = "        if atr_val > 0 and risk_per_trade_pct > 0:\n            # Path A: ATR-based risk sizing"
new5 = "        if single_position_full_equity and len(normalised) == 1:\n            # Single position: use full gross_notional (already set to 95% of max buying power)\n            notional = gross_notional * abs(w)\n            sym_leverage = max(min_symbol_leverage, min(max_symbol_leverage, int(math.ceil(leverage_target))))\n        elif atr_val > 0 and risk_per_trade_pct > 0:\n            # Path A: ATR-based risk sizing"
if new5 not in s and "        if atr_val > 0 and risk_per_trade_pct > 0:" in s:
    s = s.replace("        if atr_val > 0 and risk_per_trade_pct > 0:\n            # Path A: ATR-based risk sizing", new5, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(s)
print("sizing.py done")

# 2. runner.py - pass single_position_full_equity when alt_only
rpath = "app/engine/runner.py"
with open(rpath, "r", encoding="utf-8") as f:
    s = f.read()
old_r = "            self._sizing_result = compute_target_positions(\n                self._snapshots, vol_map, penalty_map,\n                equity=self._equity,\n                target_vol=cfg.target_portfolio_vol,\n                leverage_target=cfg.effective_leverage_target,\n                top_k_enabled=cfg.top_k_enabled,\n                top_k=cfg.top_k,\n                replace_threshold=cfg.replace_threshold,\n                min_weight_floor=cfg.min_weight_floor,\n                max_weight_cap=cfg.max_weight_cap,\n                current_symbols=self._current_symbols,\n                risk_per_trade_pct=cfg.risk_per_trade_pct,\n                max_symbol_leverage=cfg.max_symbol_leverage,\n                min_symbol_leverage=cfg.min_symbol_leverage,\n                stop_k=cfg.stop_k,\n                atr_map=_atr_map,\n            )"
new_r = "            self._sizing_result = compute_target_positions(\n                self._snapshots, vol_map, penalty_map,\n                equity=self._equity,\n                target_vol=cfg.target_portfolio_vol,\n                leverage_target=cfg.effective_leverage_target,\n                top_k_enabled=cfg.top_k_enabled,\n                top_k=cfg.top_k,\n                replace_threshold=cfg.replace_threshold,\n                min_weight_floor=cfg.min_weight_floor,\n                max_weight_cap=cfg.max_weight_cap,\n                current_symbols=self._current_symbols,\n                risk_per_trade_pct=cfg.risk_per_trade_pct,\n                max_symbol_leverage=cfg.max_symbol_leverage,\n                min_symbol_leverage=cfg.min_symbol_leverage,\n                stop_k=cfg.stop_k,\n                atr_map=_atr_map,\n                single_position_full_equity=(cfg.universe_mode == \"alt_only\"),\n            )"
if new_r not in s and old_r in s:
    s = s.replace(old_r, new_r, 1)
elif "single_position_full_equity" not in s and "atr_map=_atr_map," in s:
    s = s.replace("                atr_map=_atr_map,\n            )", "                atr_map=_atr_map,\n                single_position_full_equity=(cfg.universe_mode == \"alt_only\"),\n            )", 1)
with open(rpath, "w", encoding="utf-8") as f:
    f.write(s)
print("runner.py done")
