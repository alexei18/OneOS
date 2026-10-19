"""Make this process per-monitor DPI aware so Win32 coordinates match UIA bounds.

Without it Windows virtualizes Win32 coordinates for our process (e.g. 125 % scaling: GetWindowRect
returns 598,312 where UIA reports 748,390) and hit-tests / child rects are wrong. FINDINGS 2026-10-19.
"""

from __future__ import annotations

import ctypes

_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
_done = False


def ensure_dpi_aware() -> None:
    global _done
    if _done:
        return
    _done = True
    # Fails harmlessly (returns 0) if awareness was already set for this process.
    ctypes.windll.user32.SetProcessDpiAwarenessContext(_PER_MONITOR_AWARE_V2)
