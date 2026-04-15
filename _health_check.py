#!/usr/bin/env python3
"""Health check of the mean reversion bot structure"""

import sys
from pathlib import Path

checks = []

def check(name, condition, details=""):
    status = "✓" if condition else "✗"
    checks.append((status, name, details))
    print(f"{status} {name}" + (f" - {details}" if details else ""))

# Files exist
check("Strategy module", Path("kalshi_bot/src/kalshi_bot/mean_reversion_strategy.py").exists())
check("Backtester", Path("kalshi_bot/backtest_mean_reversion.py").exists())
check("Existing crypto_strategy", Path("kalshi_bot/src/kalshi_bot/crypto_strategy.py").exists())
check("Existing executor", Path("kalshi_bot/src/kalshi_bot/executor.py").exists())
check("Existing models", Path("kalshi_bot/src/kalshi_bot/models.py").exists())
check("Logs directory", Path("kalshi_bot/logs").is_dir())
check("Venv", Path("venv").is_dir())

# Config
check("Config .env", Path("kalshi_bot/.env").exists())

# Try imports
try:
    sys.path.insert(0, "kalshi_bot/src")
    from kalshi_bot.mean_reversion_strategy import MeanReversionStrategy
    check("Strategy imports", True, "MeanReversionStrategy ✓")
except Exception as e:
    check("Strategy imports", False, str(e)[:50])

try:
    from kalshi_bot.models import Market, Signal
    check("Models import", True)
except Exception as e:
    check("Models import", False, str(e)[:50])

print("\n" + "="*60)
passed = sum(1 for s, _, _ in checks if s == "✓")
total = len(checks)
print(f"Health: {passed}/{total} ✓")
print("="*60)

sys.exit(0 if passed == total else 1)
