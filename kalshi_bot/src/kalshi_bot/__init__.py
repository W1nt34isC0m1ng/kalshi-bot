from __future__ import annotations

from pathlib import Path
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

_VALIDATION_PACKAGE = Path(__file__).resolve().parents[3] / "src" / "kalshi_bot"
if _VALIDATION_PACKAGE.exists():
    __path__.append(str(_VALIDATION_PACKAGE))

__all__ = []
