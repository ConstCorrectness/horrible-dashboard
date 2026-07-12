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
