"""The curated bug-hunt starter set: small single-module "repos" with one planted
bug each, visible tests (what the players run) and hidden tests (what the server
also grades — the anti-memorization margin). Loaded into the task bank on boot
(`task_bank.ensure_builtin`); the generator (`task_gen.py`) adds fresh ones.

Each task's `payload` is what a player may see; `hidden` never goes over the wire.
"""

from __future__ import annotations

from typing import Any

BUG_HUNT_TASKS: list[dict[str, Any]] = [
    {
        "id": "bh-cart-total",
        "kind": "bug_hunt",
        "difficulty": "standard",
        "payload": {
            "description": (
                "The cart total is wrong when a discount code is applied. "
                "Customers with the 'SAVE10' code are being overcharged."
            ),
            "files": {
                "cart.py": (
                    "DISCOUNTS = {'SAVE10': 0.10, 'SAVE25': 0.25}\n\n\n"
                    "def item_total(price_cents, quantity):\n"
                    "    return price_cents * quantity\n\n\n"
                    "def cart_total(items, code=None):\n"
                    '    """items: list of (price_cents, quantity). Returns cents."""\n'
                    "    subtotal = sum(item_total(p, q) for p, q in items)\n"
                    "    rate = DISCOUNTS.get(code, 0.0)\n"
                    "    return int(round(subtotal * (1 + rate)))\n"
                ),
            },
            "visible_tests": {
                "test_cart.py": (
                    "from cart import cart_total\n\n\n"
                    "def test_no_discount():\n"
                    "    assert cart_total([(1000, 2)]) == 2000\n\n\n"
                    "def test_save10():\n"
                    "    assert cart_total([(1000, 1)], 'SAVE10') == 900\n"
                ),
            },
        },
        "hidden": {
            "hidden_tests": {
                "test_hidden_cart.py": (
                    "from cart import cart_total\n\n\n"
                    "def test_save25_multi():\n"
                    "    assert cart_total([(400, 3), (800, 1)], 'SAVE25') == 1500\n\n\n"
                    "def test_unknown_code():\n"
                    "    assert cart_total([(500, 1)], 'NOPE') == 500\n"
                ),
            },
        },
    },
    {
        "id": "bh-paginate",
        "kind": "bug_hunt",
        "difficulty": "standard",
        "payload": {
            "description": (
                "Pagination drops the last page when the item count isn't an "
                "exact multiple of the page size."
            ),
            "files": {
                "pages.py": (
                    "def page_count(total_items, page_size):\n"
                    "    if page_size <= 0:\n"
                    "        raise ValueError('page_size must be positive')\n"
                    "    return total_items // page_size\n\n\n"
                    "def page_slice(items, page, page_size):\n"
                    "    start = page * page_size\n"
                    "    return items[start : start + page_size]\n"
                ),
            },
            "visible_tests": {
                "test_pages.py": (
                    "from pages import page_count, page_slice\n\n\n"
                    "def test_exact():\n"
                    "    assert page_count(10, 5) == 2\n\n\n"
                    "def test_remainder():\n"
                    "    assert page_count(11, 5) == 3\n\n\n"
                    "def test_slice():\n"
                    "    assert page_slice(list(range(11)), 2, 5) == [10]\n"
                ),
            },
        },
        "hidden": {
            "hidden_tests": {
                "test_hidden_pages.py": (
                    "from pages import page_count\n\n\n"
                    "def test_empty():\n"
                    "    assert page_count(0, 5) == 0\n\n\n"
                    "def test_one_item():\n"
                    "    assert page_count(1, 50) == 1\n"
                ),
            },
        },
    },
    {
        "id": "bh-interval-merge",
        "kind": "bug_hunt",
        "difficulty": "hard",
        "payload": {
            "description": (
                "Calendar busy-time merging misses overlaps that share an exact "
                "boundary, and sometimes returns unsorted output."
            ),
            "files": {
                "intervals.py": (
                    "def merge(intervals):\n"
                    '    """Merge overlapping (start, end) intervals; touching counts."""\n'
                    "    out = []\n"
                    "    for start, end in intervals:\n"
                    "        for i, (s, e) in enumerate(out):\n"
                    "            if start < e and end > s:\n"
                    "                out[i] = (min(s, start), max(e, end))\n"
                    "                break\n"
                    "        else:\n"
                    "            out.append((start, end))\n"
                    "    return out\n"
                ),
            },
            "visible_tests": {
                "test_intervals.py": (
                    "from intervals import merge\n\n\n"
                    "def test_overlap():\n"
                    "    assert merge([(1, 4), (2, 6)]) == [(1, 6)]\n\n\n"
                    "def test_touching():\n"
                    "    assert merge([(1, 2), (2, 3)]) == [(1, 3)]\n\n\n"
                    "def test_sorted_output():\n"
                    "    assert merge([(5, 6), (1, 2)]) == [(1, 2), (5, 6)]\n"
                ),
            },
        },
        "hidden": {
            "hidden_tests": {
                "test_hidden_intervals.py": (
                    "from intervals import merge\n\n\n"
                    "def test_chain():\n"
                    "    assert merge([(1, 2), (2, 3), (3, 4)]) == [(1, 4)]\n\n\n"
                    "def test_contained():\n"
                    "    assert merge([(1, 10), (2, 3)]) == [(1, 10)]\n"
                ),
            },
        },
    },
    {
        "id": "bh-rate-limiter",
        "kind": "bug_hunt",
        "difficulty": "hard",
        "payload": {
            "description": (
                "The sliding-window rate limiter lets one extra request through "
                "at the limit, and the window never actually slides."
            ),
            "files": {
                "limiter.py": (
                    "class RateLimiter:\n"
                    '    """Allow at most `limit` hits in any `window` seconds."""\n\n'
                    "    def __init__(self, limit, window):\n"
                    "        self.limit = limit\n"
                    "        self.window = window\n"
                    "        self.hits = []\n\n"
                    "    def allow(self, now):\n"
                    "        self.hits = [t for t in self.hits if t > now + self.window]\n"
                    "        if len(self.hits) <= self.limit:\n"
                    "            self.hits.append(now)\n"
                    "            return True\n"
                    "        return False\n"
                ),
            },
            "visible_tests": {
                "test_limiter.py": (
                    "from limiter import RateLimiter\n\n\n"
                    "def test_limit_enforced():\n"
                    "    rl = RateLimiter(2, 10)\n"
                    "    assert rl.allow(0) and rl.allow(1)\n"
                    "    assert not rl.allow(2)\n\n\n"
                    "def test_window_slides():\n"
                    "    rl = RateLimiter(1, 10)\n"
                    "    assert rl.allow(0)\n"
                    "    assert not rl.allow(5)\n"
                    "    assert rl.allow(11)\n"
                ),
            },
        },
        "hidden": {
            "hidden_tests": {
                "test_hidden_limiter.py": (
                    "from limiter import RateLimiter\n\n\n"
                    "def test_exact_boundary():\n"
                    "    rl = RateLimiter(1, 10)\n"
                    "    assert rl.allow(0)\n"
                    "    assert not rl.allow(10)\n"
                    "    assert rl.allow(10.01)\n\n\n"
                    "def test_burst_then_recover():\n"
                    "    rl = RateLimiter(3, 5)\n"
                    "    assert all(rl.allow(t) for t in (0, 1, 2))\n"
                    "    assert not rl.allow(3)\n"
                    "    assert rl.allow(8)\n"
                ),
            },
        },
    },
]
