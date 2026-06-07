p = "app/engine/runner.py"
with open(p, "r", encoding="utf-8") as f:
    s = f.read()
old = "                atr_map=_atr_map,\n            )"
new = "                atr_map=_atr_map,\n                single_position_full_equity=(cfg.universe_mode == \"alt_only\"),\n            )"
if new in s:
    print("already there")
elif old in s:
    s = s.replace(old, new, 1)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)
    print("done")
else:
    print("pattern not found")
