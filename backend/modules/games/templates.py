"""Starter loadout templates for the onboarding wizard's "build your first
harness" step — small, readable, and genuinely useful, so the first thing a new
player ships actually helps their agent play."""

from __future__ import annotations

from typing import Any

_TTT_SCANNER = '''\
def run(args, obs):
    """Find my winning cell and the opponent's threat on a tic-tac-toe board."""
    board = obs.get("board") or [None] * 9
    lines = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]

    def open_cell(mark):
        for line in lines:
            cells = [board[i] for i in line]
            if cells.count(mark) == 2 and cells.count(None) == 1:
                return line[cells.index(None)]
        return None

    me = "X" if board.count("X") == board.count("O") else "O"
    them = "O" if me == "X" else "X"
    return {"win_at": open_cell(me), "block_at": open_cell(them)}
'''

_C4_THREATS = '''\
def run(args, obs):
    """Score each connect-four column: does dropping there win or must I block?"""
    board = obs.get("board") or []          # top row first
    rows, cols = len(board), len(board[0]) if board else 7
    grid = [list(r) for r in reversed(board)]  # bottom-first for gravity

    def drop_row(col):
        for r in range(rows):
            if grid[r][col] is None:
                return r
        return None

    def wins(mark, r, c):
        grid[r][c] = mark
        try:
            for dr, dc in ((0,1),(1,0),(1,1),(1,-1)):
                for k0 in range(-3, 1):
                    if all(0 <= r+dr*(k0+k) < rows and 0 <= c+dc*(k0+k) < cols
                           and grid[r+dr*(k0+k)][c+dc*(k0+k)] == mark for k in range(4)):
                        return True
            return False
        finally:
            grid[r][c] = None

    flat = [cell for row in grid for cell in row]
    me = "R" if flat.count("R") == flat.count("Y") else "Y"
    them = "Y" if me == "R" else "R"
    report = {}
    for col in range(cols):
        r = drop_row(col)
        if r is None:
            continue
        report[col] = {"i_win": wins(me, r, col), "must_block": wins(them, r, col)}
    return report
'''

_RAG_KEYWORDS = '''\
def run(args, obs):
    """Pick the best-matching corpus sentence for a question (better than nothing —
    replace me with real retrieval)."""
    import re
    question = str(args.get("question") or "")
    words = set(re.sub(r"[^a-z0-9]+", " ", question.lower()).split())
    best, score = "", 0
    for doc in obs.get("docs") or []:
        for sentence in re.split(r"(?<=[.!?])\\s+", str(doc.get("text") or "")):
            hits = len(words & set(re.sub(r"[^a-z0-9]+", " ", sentence.lower()).split()))
            if hits > score:
                best, score = sentence.strip(), hits
    return {"best_sentence": best, "matched_words": score}
'''


_FIGHTER_BOT = '''\
def run(args, obs):
    """Return this tick's move: up/down/left/right/jump/crouch_block/light/heavy/
    special/idle. obs has p=[me, opp] with x/y/hp/meter, plus round info."""
    me, opp = obs["p"][obs["seat"]], obs["p"][1 - obs["seat"]]
    dist = abs(me["x"] - opp["x"])
    if me["meter"] >= 50 and dist <= 90:
        return "special"
    if dist <= 30:
        return "heavy"
    if dist <= 40:
        return "light"
    return "right" if opp["x"] > me["x"] else "left"
'''


_VIZDOOM_BOT = '''\
def run(args, obs):
    """Return this tick's action for ViZDoom defend_the_center: idle/turn_left/
    turn_right/attack. The frame is an opaque JPEG (no vision here — keep it fast),
    so sweep-and-shoot off the HUD: fire while you have ammo, and rotate every few
    ticks to bring the next imp into your sights."""
    legal = [a["id"] for a in obs.get("legal_actions", [])]
    hud = obs.get("hud") or {}
    tick = int(obs.get("tick", 0))
    if hud.get("ammo", 0) <= 0 and "turn_right" in legal:
        return "turn_right"          # out of ammo: keep facing new targets
    if tick % 3 == 2 and "turn_right" in legal:
        return "turn_right"          # sweep to acquire
    if "attack" in legal:
        return "attack"
    return "idle"
'''


_VIZDOOM_DUEL_BOT = '''\
def run(args, obs):
    """Return this tick's action for the networked ViZDoom Duel on the cig arena:
    idle/attack/use/turn_left/turn_right/move_left/move_right/move_forward/
    move_backward. The frame is an opaque JPEG (no vision here — keep it fast), so
    this is a pressure-and-circle-strafe brawler: keep firing while you have ammo,
    press forward to close, and alternate strafe + turn every few ticks so you keep
    weaving and sweeping the arena for the other marine instead of standing still."""
    legal = [a["id"] for a in obs.get("legal_actions", [])]
    hud = obs.get("hud") or {}
    tick = int(obs.get("tick", 0))

    def pick(*prefs):
        for a in prefs:
            if a in legal:
                return a
        return "idle"

    # Out of ammo: stop firing, reposition and hunt a new angle.
    if hud.get("ammo", 0) <= 0:
        return pick("turn_right", "move_forward", "idle")
    phase = tick % 4
    if phase == 0:
        return pick("attack")
    if phase == 1:
        return pick("move_left", "attack")
    if phase == 2:
        return pick("turn_right", "attack")
    return pick("move_right", "move_forward", "attack")
'''


# --- starters for the open-action (code-submission) games -------------------
# These games are graded on *submitted content* (a bot program, a patch, a golfed
# solution, tests, a feature transform), so their harness is context + a helper
# tool that reads the observation — not a per-tick bot.

_ARENA_HELPER = '''\
def run(args, obs):
    """Read where the arena match stands so you can iterate your bot(obs): am I
    behind on round wins, and is there a round to study? Improve on
    obs['starter_bot'] — chase the nearest pellet, and step onto the opponent's
    just-vacated cell to steal points."""
    seat = int(obs.get("seat", 0))
    wins = obs.get("round_wins") or [0, 0]
    me, opp = wins[seat], wins[1 - seat]
    return {
        "round": obs.get("round"),
        "rounds": obs.get("rounds"),
        "my_round_wins": me,
        "opp_round_wins": opp,
        "behind": me < opp,
        "have_last_round": obs.get("last_round") is not None,
    }
'''

_BUGHUNT_HELPER = '''\
def run(args, obs):
    """Triage the last bug-hunt attempt: which tests are still failing and how many
    tries are left. Patch the smallest diff that turns them green, then resubmit the
    whole files map — read obs['description'] and obs['visible_tests'] for intent."""
    attempts = obs.get("attempts") or []
    if not attempts:
        return {"note": "no attempts yet - submit a fix, then read failures here",
                "attempts_left": obs.get("attempts_left")}
    last = attempts[-1]
    return {
        "all_green": bool(last.get("green")),
        "passed": last.get("passed"),
        "failed": last.get("failed"),
        "attempts_left": obs.get("attempts_left"),
    }
'''

_CODEGOLF_HELPER = '''\
def run(args, obs):
    """Byte-count a candidate solution (pass it as `code`) before you submit:
    correctness first (it must satisfy obs['public_examples']), then fewest bytes
    wins ties. Compare shorter rewrites by their byte length."""
    code = str(args.get("code") or "")
    return {
        "bytes": len(code.encode("utf-8")),
        "chars": len(code),
        "signature": obs.get("signature"),
        "n_public_examples": len(obs.get("public_examples") or []),
    }
'''

_TESTDUEL_HELPER = '''\
def run(args, obs):
    """Parse the target signature so you can cover it: the function name and its
    parameter names, plus the current phase (write a correct impl first, then tests
    that pass a correct impl but break a buggy one)."""
    import re
    sig = str(obs.get("signature") or "")
    m = re.search(r"(\\w+)\\s*\\(([^)]*)\\)", sig)
    name = m.group(1) if m else None
    params = []
    if m and m.group(2).strip():
        for p in m.group(2).split(","):
            params.append(p.strip().split(":")[0].split("=")[0].strip())
    return {"function": name, "params": params, "phase": obs.get("phase")}
'''

_TABULARFE_HELPER = '''\
def run(args, obs):
    """List the dataset columns and dtypes from obs['data_samples'] so you know what
    to engineer: build ratios/interactions/log-transforms of numeric columns and
    encode categoricals to move obs['metric']. Start from obs['starter_code']."""
    samples = obs.get("data_samples") or []
    if not samples:
        return {"columns": [], "note": "no samples in observation"}
    row = samples[0]
    dtypes = {k: type(v).__name__ for k, v in row.items()}
    return {"columns": list(dtypes), "dtypes": dtypes, "metric": obs.get("metric")}
'''


_TTT_FORKS = '''\
def run(args, obs):
    """Find fork cells: empty squares that would create TWO winning threats at
    once — mine to play, the opponent's to deny."""
    board = obs.get("board") or [None] * 9
    lines = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    me = "X" if board.count("X") == board.count("O") else "O"
    them = "O" if me == "X" else "X"

    def threats(mark, cell):
        count = 0
        for line in lines:
            if cell not in line:
                continue
            cells = [mark if i == cell else board[i] for i in line]
            if cells.count(mark) == 2 and cells.count(None) == 1:
                count += 1
        return count

    empty = [i for i in range(9) if board[i] is None]
    return {
        "my_forks": [i for i in empty if threats(me, i) >= 2],
        "their_forks": [i for i in empty if threats(them, i) >= 2],
    }
'''

_HOLDEM_POT_ODDS = '''\
def run(args, obs):
    """Pot odds for calling `to_call` chips: the equity needed to break even."""
    to_call = float(args.get("to_call") or 0)
    pot = float(obs.get("pot") or 0)
    if to_call <= 0:
        return {"break_even_equity": 0.0, "note": "nothing to call - checking is free"}
    return {
        "break_even_equity": round(to_call / (pot + to_call), 3),
        "pot_after_call": pot + to_call,
        "note": "worth calling only if your equity beats break_even_equity",
    }
'''

_HOLDEM_STRENGTH = '''\
def run(args, obs):
    """A rough read of my hand: preflop hole-card score (4-40), postflop the
    made-hand category from my hole cards + the board. Heuristic, not a solver."""
    from collections import Counter
    ranks = "23456789TJQKA"
    hole = obs.get("hole") or []
    board = obs.get("board") or []
    vals = sorted((ranks.index(c[0]) + 2 for c in hole), reverse=True)
    suited = len(hole) == 2 and hole[0][1] == hole[1][1]
    if not board:
        score = vals[0] + vals[1]
        if vals[0] == vals[1]:
            score += 12
        if suited:
            score += 3
        if 1 <= vals[0] - vals[1] <= 2:
            score += 2
        return {"street": "preflop", "score_4_to_40": score,
                "pocket_pair": vals[0] == vals[1], "suited": suited}
    cards = hole + board
    counts = Counter(c[0] for c in cards).most_common()
    top = counts[0][1]
    second = counts[1][1] if len(counts) > 1 else 0
    flush = max(Counter(c[1] for c in cards).values()) >= 5
    uniq = sorted({ranks.index(c[0]) + 2 for c in cards})
    if 14 in uniq:
        uniq = [1] + uniq  # the wheel: ace plays low too
    straight = any(all(v + k in uniq for k in range(5)) for v in uniq)
    if top == 4: category = "four of a kind"
    elif top == 3 and second >= 2: category = "full house"
    elif flush: category = "flush"
    elif straight: category = "straight"
    elif top == 3: category = "three of a kind"
    elif top == 2 and second == 2: category = "two pair"
    elif top == 2: category = "pair"
    else: category = "high card"
    return {"street": obs.get("street"), "category": category,
            "board_cards": len(board)}
'''


def loadout_templates() -> list[dict[str, Any]]:
    """Template descriptors: `{id, game_id, title, blurb, loadout}` (wire shape)."""
    return [
        {
            "id": "fighter-rushdown",
            "game_id": "fighter",
            "title": "Rushdown bot",
            "blurb": "A fighter bot (fighter.bot): close the distance, poke with light, punish with heavy, and special when you have meter.",
            "loadout": {
                "game_id": "fighter",
                "context": "",
                "tools": [
                    {
                        "name": "fighter.bot",
                        "description": "Returns this tick's action for the ranked fighter (runs every frame — keep it fast, no model).",
                        "code": _FIGHTER_BOT,
                        "parameters": {},
                        "required": [],
                    }
                ],
                "model": None,
            },
        },
        {
            "id": "vizdoom-sweeper",
            "game_id": "vizdoom_toy",
            "title": "Center sweeper",
            "blurb": "A ViZDoom bot (vizdoom_toy.bot): stand your ground, sweep the arena, and gun down every imp before it reaches you.",
            "loadout": {
                "game_id": "vizdoom_toy",
                "context": "",
                "tools": [
                    {
                        "name": "vizdoom_toy.bot",
                        "description": "Returns this tick's action for ranked ViZDoom (runs every frame — keep it fast, no model).",
                        "code": _VIZDOOM_BOT,
                        "parameters": {},
                        "required": [],
                    }
                ],
                "model": None,
            },
        },
        {
            "id": "vizdoom-brawler",
            "game_id": "vizdoom_duel",
            "title": "Circle-strafe brawler",
            "blurb": "A ViZDoom Duel bot (vizdoom_duel.bot): a real 1v1 deathmatch on a shared map — keep firing, close the distance, and weave so you're never a standing target.",
            "loadout": {
                "game_id": "vizdoom_duel",
                "context": "",
                "tools": [
                    {
                        "name": "vizdoom_duel.bot",
                        "description": "Returns this tick's action for the networked ViZDoom Duel (runs every frame — keep it fast, no model).",
                        "code": _VIZDOOM_DUEL_BOT,
                        "parameters": {},
                        "required": [],
                    }
                ],
                "model": None,
            },
        },
        {
            "id": "arena-greedy",
            "game_id": "arena",
            "title": "Greedy pellet-seeker",
            "blurb": "An Arena starter: submit a bot(obs) that chases the nearest pellet and contests the opponent's cell, plus a tool to read the score so you can iterate.",
            "loadout": {
                "game_id": "arena",
                "context": (
                    "Arena is a bot-programming duel: you submit bot(obs) returning "
                    "up/down/left/right/stay, and the server simulates the rounds. Start "
                    "from obs['starter_bot'] and make it greedy — each tick step one cell "
                    "toward the nearest pellet, and if the opponent just vacated a cell you "
                    "can reach, take it to steal points. Call round_report to see if you're "
                    "behind and study obs['last_round'] to iterate."
                ),
                "tools": [
                    {
                        "name": "round_report",
                        "description": "Round wins so far and whether you're behind, to guide your next bot(obs).",
                        "code": _ARENA_HELPER,
                        "parameters": {},
                        "required": [],
                    }
                ],
                "model": None,
            },
        },
        {
            "id": "bughunt-triage",
            "game_id": "bug_hunt",
            "title": "Failing-test triage",
            "blurb": "A Bug Hunt starter: read the description and visible tests, patch the smallest diff, and use failing_tests to see what's still red between attempts.",
            "loadout": {
                "game_id": "bug_hunt",
                "context": (
                    "Bug Hunt: fix bugs across obs['files'] so the tests pass. Read "
                    "obs['description'] and obs['visible_tests'] for intended behavior, "
                    "change the fewest lines, and resubmit the whole files map. After each "
                    "attempt call failing_tests to see which tests are still failing and "
                    "how many attempts remain."
                ),
                "tools": [
                    {
                        "name": "failing_tests",
                        "description": "Summarize the latest attempt: tests passed/failed and attempts left.",
                        "code": _BUGHUNT_HELPER,
                        "parameters": {},
                        "required": [],
                    }
                ],
                "model": None,
            },
        },
        {
            "id": "codegolf-golfer",
            "game_id": "code_golf",
            "title": "Correct-then-shortest",
            "blurb": "A Code Golf starter: pass the public examples first, then shrink — byte_count measures each candidate so you can chase the fewest bytes.",
            "loadout": {
                "game_id": "code_golf",
                "context": (
                    "Code Golf: implement obs['signature'] to satisfy obs['public_examples'] "
                    "(correctness first — a wrong-but-short answer loses), then rewrite to "
                    "the fewest bytes. Call byte_count on each candidate to compare lengths; "
                    "prefer lambdas, comprehensions, and no temporary variables."
                ),
                "tools": [
                    {
                        "name": "byte_count",
                        "description": "Byte-count a candidate solution before submitting.",
                        "code": _CODEGOLF_HELPER,
                        "parameters": {
                            "code": {
                                "type": "string",
                                "description": "a candidate solution to measure",
                            }
                        },
                        "required": ["code"],
                    }
                ],
                "model": None,
            },
        },
        {
            "id": "testduel-coverage",
            "game_id": "test_duel",
            "title": "Cover-and-break",
            "blurb": "A Test Duel starter: write a correct implementation, then tests that pass a correct impl but break a buggy one — spec_signature pulls the function + params to cover.",
            "loadout": {
                "game_id": "test_duel",
                "context": (
                    "Test Duel has two phases (obs['phase']). First write a correct impl of "
                    "obs['signature'] from obs['spec']. Then write unit tests that PASS a "
                    "correct impl but BREAK a buggy one — cover edge cases (empty, zero, "
                    "negative, boundary, wrong types). Call spec_signature for the function "
                    "name and parameters. Invalid tests (failing the reference impl) score 0."
                ),
                "tools": [
                    {
                        "name": "spec_signature",
                        "description": "Parse the target function name and parameters from the signature.",
                        "code": _TESTDUEL_HELPER,
                        "parameters": {},
                        "required": [],
                    }
                ],
                "model": None,
            },
        },
        {
            "id": "tabularfe-features",
            "game_id": "tabular_fe",
            "title": "Feature builder",
            "blurb": "A Tabular Feature Engineering starter: inspect the columns, then build ratios/interactions/transforms to move the metric — feature_columns lists what you have to work with.",
            "loadout": {
                "game_id": "tabular_fe",
                "context": (
                    "Tabular Feature Engineering: transform the DataFrame in your submission "
                    "to improve obs['metric']. Call feature_columns to list the columns and "
                    "dtypes from obs['data_samples'], then add ratios, interactions, and "
                    "log/scale transforms of numeric columns and encode categoricals. Start "
                    "from obs['starter_code']."
                ),
                "tools": [
                    {
                        "name": "feature_columns",
                        "description": "List dataset columns and dtypes from the observation samples.",
                        "code": _TABULARFE_HELPER,
                        "parameters": {},
                        "required": [],
                    }
                ],
                "model": None,
            },
        },
        {
            "id": "ttt-scanner",
            "game_id": "tictactoe",
            "title": "Board scanner",
            "blurb": "A tool that finds your winning cell and the square you must block — the classic first harness.",
            "loadout": {
                "game_id": "tictactoe",
                "context": (
                    "Always call board_scanner first. If win_at is a number, choose "
                    "that cell. Else if block_at is a number, choose that. Otherwise "
                    "prefer the center (4), then corners (0, 2, 6, 8)."
                ),
                "tools": [
                    {
                        "name": "board_scanner",
                        "description": "Returns win_at (your winning cell) and block_at (the opponent's threat) for the current board.",
                        "code": _TTT_SCANNER,
                        "parameters": {},
                        "required": [],
                    }
                ],
                "model": None,
            },
        },
        {
            "id": "c4-threats",
            "game_id": "connect_four",
            "title": "Threat scout",
            "blurb": "Scores every column: instant wins and forced blocks, so the agent never misses either.",
            "loadout": {
                "game_id": "connect_four",
                "context": (
                    "Call column_threats first. Play any column with i_win true; "
                    "else any with must_block true; else prefer the center column 3."
                ),
                "tools": [
                    {
                        "name": "column_threats",
                        "description": "For each legal column: whether dropping there wins immediately (i_win) or blocks the opponent's win (must_block).",
                        "code": _C4_THREATS,
                        "parameters": {},
                        "required": [],
                    }
                ],
                "model": None,
            },
        },
        {
            "id": "ttt-tactician",
            "game_id": "tictactoe",
            "title": "Tactician — scanner + fork finder",
            "blurb": "TWO tools working together: the scanner for wins/blocks, a fork finder for double threats — your context tells the model which to trust first.",
            "loadout": {
                "game_id": "tictactoe",
                "context": (
                    "Call board_scanner first. If win_at is a number, choose that "
                    "cell. Else if block_at is a number, choose that. Otherwise "
                    "call fork_finder: play any cell in my_forks; else block a "
                    "cell in their_forks. Failing all that, prefer the center "
                    "(4), then corners (0, 2, 6, 8)."
                ),
                "tools": [
                    {
                        "name": "board_scanner",
                        "description": "Returns win_at (your winning cell) and block_at (the opponent's threat) for the current board.",
                        "code": _TTT_SCANNER,
                        "parameters": {},
                        "required": [],
                    },
                    {
                        "name": "fork_finder",
                        "description": "Returns my_forks (cells creating two threats at once) and their_forks (fork cells to deny the opponent).",
                        "code": _TTT_FORKS,
                        "parameters": {},
                        "required": [],
                    },
                ],
                "model": None,
            },
        },
        {
            "id": "holdem-calculator",
            "game_id": "holdem",
            "title": "Pot odds + hand strength",
            "blurb": "Two hold'em tools — one takes model-supplied arguments (pot_odds needs to_call), one reads the observation. Shows how `parameters` work.",
            "loadout": {
                "game_id": "holdem",
                "context": (
                    "Call hand_strength first. If the observation shows to_call > "
                    "0, call pot_odds with that exact to_call amount. Raise with "
                    "two pair or better (or a preflop score of 20+); call when "
                    "your hand clearly beats break_even_equity; otherwise fold. "
                    "With no bet to face, check weak hands and bet strong ones."
                ),
                "tools": [
                    {
                        "name": "hand_strength",
                        "description": "Rates my hand: preflop hole-card score, postflop the made-hand category from hole + board.",
                        "code": _HOLDEM_STRENGTH,
                        "parameters": {},
                        "required": [],
                    },
                    {
                        "name": "pot_odds",
                        "description": "Break-even equity for calling a bet. Pass the to_call amount from the observation.",
                        "code": _HOLDEM_POT_ODDS,
                        "parameters": {
                            "to_call": {
                                "type": "number",
                                "description": "the chips you must put in to call (obs.to_call)",
                            }
                        },
                        "required": ["to_call"],
                    },
                ],
                "model": None,
            },
        },
        {
            "id": "rag-keywords",
            "game_id": "rag_race",
            "title": "Keyword retriever",
            "blurb": "A per-question retrieval tool for the RAG race — the baseline to beat with real search.",
            "loadout": {
                "game_id": "rag_race",
                "context": (
                    "For each question, call find_answer with the question text and "
                    "quote the best_sentence it returns, trimmed to the answer cap."
                ),
                "tools": [
                    {
                        "name": "find_answer",
                        "description": "Finds the corpus sentence that best matches a question.",
                        "code": _RAG_KEYWORDS,
                        "parameters": {
                            "question": {
                                "type": "string",
                                "description": "the question text",
                            }
                        },
                        "required": ["question"],
                    }
                ],
                "model": None,
            },
        },
    ]


def _bot_tool(body: dict[str, Any], game_id: str) -> dict[str, Any] | None:
    """The `<game>.bot` / `bot` tool in a template body, if it has one. A template
    written around one is a **coded** starter: it has no context and the tool is the
    whole policy."""
    for tool in body.get("tools") or []:
        name = str(tool.get("name") or "")
        if name == "bot" or name == f"{game_id}.bot":
            return tool
    return None


def template_kind(template: dict[str, Any]) -> str:
    """Which harness a template is for. Derived rather than declared: a template
    whose tool list contains the bot tool *is* a coded policy, and one that doesn't
    is context + helpers for the model. Deriving it keeps the two from disagreeing
    when a template is edited."""
    from backend.modules.games.loadout import CODED, LLM

    body = template.get("loadout") or {}
    return CODED if _bot_tool(body, str(template.get("game_id") or "")) else LLM


def default_harness_for(game_id: str, kind: str) -> dict[str, Any] | None:
    """The shipped starter harness (wire dict) of `kind` for `game_id` — the
    **first** matching template. Used to seed the default so a fresh player already
    has a working harness. None if the game ships no template of that kind.

    A coded starter is returned in the coded harness's own shape (`bot_code`), not
    as a tool list: the tool wrapper was only ever the old storage's way of holding
    a policy.
    """
    from backend.modules.games.loadout import CODED

    for template in loadout_templates():
        if template["game_id"] != game_id or template_kind(template) != kind:
            continue
        body = dict(template["loadout"])
        if kind == CODED:
            tool = _bot_tool(body, game_id) or {}
            return {"bot_code": str(tool.get("code") or "")}
        return body
    return None
