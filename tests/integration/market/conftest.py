from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Live tests run only when explicitly selected with `-m live`."""
    expr = config.getoption("-m") or ""
    if "live" in expr and "not live" not in expr:
        return
    skip = pytest.mark.skip(reason="live test: run with -m live --force-enable-socket")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
