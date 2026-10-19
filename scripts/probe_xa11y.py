"""Phase 0 probe: list top-level windows via xa11y and dump 2 levels of one window."""
import sys
import xa11y

sys.stdout.reconfigure(encoding="utf-8")  # window titles contain non-cp1252 chars

apps = [(a, w) for a in xa11y.App.list() for w in a.windows()]
for app, win in apps:
    print(f"{app.pid:>6}  {app.name:<28} {win.role:<8} {win.name!r}")
prefer = ("Notepad", "Explorer", "Calculator")
app, win = next(((a, w) for a, w in apps if a.name.lower().startswith(tuple(p.lower() for p in prefer))), apps[0])
print(f"\n--- {app.name} / {win.name!r} (max_depth=2) ---")
print(win.dump(max_depth=2))
