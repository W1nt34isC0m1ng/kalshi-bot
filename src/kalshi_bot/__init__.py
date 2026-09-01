from __future__ import annotations

from pathlib import Path
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

_LIVE_PACKAGE = Path(__file__).resolve().parents[2] / "kalshi_bot" / "src" / "kalshi_bot"
if _LIVE_PACKAGE.exists():
    __path__.append(str(_LIVE_PACKAGE))
