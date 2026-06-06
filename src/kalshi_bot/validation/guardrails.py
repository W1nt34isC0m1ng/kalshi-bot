from __future__ import annotations

import subprocess


class GuardrailError(Exception):
    pass


def assert_on_main_branch() -> None:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    branch = result.stdout.strip()
    if branch != "main":
        raise GuardrailError(
            f"BLOCKED: refusing to promote from branch '{branch}'. "
            "Checkout main before running validate.py."
        )


def check_market_collision(
    strategies: dict[str, object],
    markets: list,
) -> list[str]:
    ticker_to_names: dict[str, set[str]] = {}
    for name, strategy in strategies.items():
        for market in markets:
            sig = strategy.evaluate(market)
            if sig is not None:
                ticker_to_names.setdefault(sig.ticker, set()).add(name)

    warnings = []
    for ticker, names in ticker_to_names.items():
        if len(names) >= 2:
            quoted = " and ".join(repr(n) for n in sorted(names))
            warnings.append(
                f"WARNING: market collision on {ticker} — "
                f"Strategies {quoted} are all signalling this ticker. "
                "They will compete for the same fill. Verify this is intentional."
            )
    return warnings
