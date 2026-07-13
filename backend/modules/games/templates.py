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
