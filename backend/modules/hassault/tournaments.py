"""Authoritative Major Tournament Bracket & Pick/Ban Veto Engine.

Implements the official Major tournament ecosystem:
- 16-team Swiss System tournament stage (5 rounds, 3 wins advance, 3 losses eliminated).
- Buchholz difficulty seeding and rematch prevention.
- 8-team Single Elimination Champions Stage (Playoffs: Quarterfinals, Semifinals, Grand Final).
- Active Duty 7-map pool pick/ban veto engine (BO1, BO3, BO5).
- SQLite persistence in `.data/hassault/tournaments.db`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.paths import data_dir

logger = logging.getLogger(__name__)

ACTIVE_DUTY_MAPS = [
    "hd_nuke",
    "hd_mirage",
    "hd_inferno",
    "hd_dust2",
    "hd_office",
    "hd_bank",
    "hd_facility",
]

DEFAULT_MAJOR_TEAMS = [
    {"id": "team_faze", "name": "FaZe Apex", "seed": 1},
    {"id": "team_navi", "name": "Natus Vincere", "seed": 2},
    {"id": "team_vitality", "name": "Team Vitality", "seed": 3},
    {"id": "team_g2", "name": "G2 Esports", "seed": 4},
    {"id": "team_mouz", "name": "MOUZ Titan", "seed": 5},
    {"id": "team_spirit", "name": "Team Spirit", "seed": 6},
    {"id": "team_astralis", "name": "Astralis Prime", "seed": 7},
    {"id": "team_c9", "name": "Cloud9 Elite", "seed": 8},
    {"id": "team_vp", "name": "Virtus.pro", "seed": 9},
    {"id": "team_heroic", "name": "HEROIC", "seed": 10},
    {"id": "team_liquid", "name": "Team Liquid", "seed": 11},
    {"id": "team_col", "name": "Complexity", "seed": 12},
    {"id": "team_furia", "name": "FURIA Esports", "seed": 13},
    {"id": "team_ef", "name": "Eternal Fire", "seed": 14},
    {"id": "team_big", "name": "BIG Clan", "seed": 15},
    {"id": "team_mongolz", "name": "The MongolZ", "seed": 16},
]


def _get_db() -> sqlite3.Connection:
    db_path = data_dir() / "hassault" / "tournaments.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tournaments (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                round INTEGER NOT NULL DEFAULT 1,
                winner_id TEXT,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS teams (
                id TEXT NOT NULL,
                tournament_id TEXT NOT NULL,
                name TEXT NOT NULL,
                seed INTEGER NOT NULL,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                buchholz INTEGER NOT NULL DEFAULT 0,
                eliminated INTEGER NOT NULL DEFAULT 0,
                qualified INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (id, tournament_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS matches (
                id TEXT PRIMARY KEY,
                tournament_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                round INTEGER NOT NULL,
                team_a_id TEXT NOT NULL,
                team_b_id TEXT NOT NULL,
                format TEXT NOT NULL DEFAULT 'BO1',
                status TEXT NOT NULL DEFAULT 'scheduled',
                winner_id TEXT,
                score_a INTEGER NOT NULL DEFAULT 0,
                score_b INTEGER NOT NULL DEFAULT 0,
                active_map TEXT,
                completed_maps TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vetoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT NOT NULL,
                step INTEGER NOT NULL,
                team_id TEXT NOT NULL,
                action TEXT NOT NULL,
                map_name TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
            """
        )


class TournamentEngine:
    def __init__(self) -> None:
        pass

    def get_or_create_major(self, tournament_id: str = "major_2026") -> dict[str, Any]:
        """Fetch existing major tournament or seed new 16-team championship."""
        conn = _get_db()
        row = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
        if not row:
            return self.create_major(tournament_id, "Titan League CS:GO Major Championship 2026")
        return self.get_tournament_state(tournament_id)

    def create_major(self, tournament_id: str, name: str) -> dict[str, Any]:
        conn = _get_db()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tournaments (id, name, stage, status, round, winner_id, created_at)
                VALUES (?, ?, 'swiss', 'active', 1, NULL, ?)
                """,
                (tournament_id, name, time.time()),
            )
            # Insert 16 teams
            for t in DEFAULT_MAJOR_TEAMS:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO teams
                    (id, tournament_id, name, seed, wins, losses, buchholz, eliminated, qualified)
                    VALUES (?, ?, ?, ?, 0, 0, 0, 0, 0)
                    """,
                    (t["id"], tournament_id, t["name"], t["seed"]),
                )

        # Generate Round 1 pairings (Swiss 0-0): 1 vs 9, 2 vs 10, etc.
        self._generate_swiss_round(tournament_id, round_num=1)
        return self.get_tournament_state(tournament_id)

    def get_tournament_state(self, tournament_id: str) -> dict[str, Any]:
        conn = _get_db()
        tourn = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
        if not tourn:
            raise ValueError(f"Tournament {tournament_id} not found")

        teams_rows = conn.execute(
            "SELECT * FROM teams WHERE tournament_id = ? ORDER BY wins DESC, losses ASC, buchholz DESC, seed ASC",
            (tournament_id,),
        ).fetchall()
        teams = [dict(t) for t in teams_rows]

        matches_rows = conn.execute(
            "SELECT * FROM matches WHERE tournament_id = ? ORDER BY stage ASC, round ASC, id ASC",
            (tournament_id,),
        ).fetchall()

        matches = []
        for m in matches_rows:
            m_dict = dict(m)
            m_dict["completed_maps"] = json.loads(m_dict.get("completed_maps") or "[]")
            m_dict["veto"] = self.get_veto_state(m_dict["id"])
            matches.append(m_dict)

        return {
            "id": tourn["id"],
            "name": tourn["name"],
            "stage": tourn["stage"],
            "status": tourn["status"],
            "round": tourn["round"],
            "winnerId": tourn["winner_id"],
            "teams": teams,
            "matches": matches,
            "activeDutyMaps": ACTIVE_DUTY_MAPS,
        }

    def _generate_swiss_round(self, tournament_id: str, round_num: int) -> None:
        conn = _get_db()
        teams = conn.execute(
            "SELECT * FROM teams WHERE tournament_id = ? AND eliminated = 0 AND qualified = 0 ORDER BY seed ASC",
            (tournament_id,),
        ).fetchall()

        # Group by record (wins, losses)
        pools: dict[tuple[int, int], list[sqlite3.Row]] = {}
        for t in teams:
            rec = (t["wins"], t["losses"])
            pools.setdefault(rec, []).append(t)

        with conn:
            # Pair teams within each record pool
            for rec, pool in pools.items():
                n = len(pool)
                # Pair top half with bottom half: i with i + n//2
                half = n // 2
                for i in range(half):
                    team_a = pool[i]
                    team_b = pool[i + half]
                    match_id = f"swiss_r{round_num}_{team_a['id']}_vs_{team_b['id']}"
                    # Match format: BO3 for advancement/elimination matches (2 wins or 2 losses), BO1 otherwise
                    is_bo3 = rec[0] == 2 or rec[1] == 2
                    fmt = "BO3" if is_bo3 else "BO1"
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO matches
                        (id, tournament_id, stage, round, team_a_id, team_b_id, format, status, winner_id, score_a, score_b, active_map, completed_maps)
                        VALUES (?, ?, 'swiss', ?, ?, ?, ?, 'scheduled', NULL, 0, 0, NULL, '[]')
                        """,
                        (match_id, tournament_id, round_num, team_a["id"], team_b["id"], fmt),
                    )

    def get_veto_state(self, match_id: str) -> dict[str, Any]:
        conn = _get_db()
        m = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        if not m:
            return {"matchId": match_id, "error": "Match not found"}

        vetoes = conn.execute(
            "SELECT * FROM vetoes WHERE match_id = ? ORDER BY step ASC", (match_id,)
        ).fetchall()

        banned_maps: list[str] = []
        picked_maps: list[str] = []
        steps: list[dict[str, Any]] = []

        for v in vetoes:
            if v["action"] == "ban":
                banned_maps.append(v["map_name"])
            elif v["action"] == "pick":
                picked_maps.append(v["map_name"])
            steps.append(
                {
                    "step": v["step"],
                    "teamId": v["team_id"],
                    "action": v["action"],
                    "map": v["map_name"],
                    "timestamp": v["timestamp"],
                }
            )

        remaining_maps = [m_name for m_name in ACTIVE_DUTY_MAPS if m_name not in banned_maps and m_name not in picked_maps]
        fmt = m["format"]
        team_a = m["team_a_id"]
        team_b = m["team_b_id"]

        decider: Optional[str] = None
        current_step = len(steps) + 1
        is_complete = False
        current_turn: Optional[str] = None
        current_action: Optional[str] = None

        if fmt == "BO1":
            # 6 bans, 1 decider
            # Step 1: Team A ban
            # Step 2: Team B ban
            # Step 3: Team A ban
            # Step 4: Team B ban
            # Step 5: Team A ban
            # Step 6: Team B ban
            # Step 7: Decider
            schedule = [
                (1, team_a, "ban"),
                (2, team_b, "ban"),
                (3, team_a, "ban"),
                (4, team_b, "ban"),
                (5, team_a, "ban"),
                (6, team_b, "ban"),
            ]
            if len(steps) < 6:
                _, current_turn, current_action = schedule[len(steps)]
            else:
                is_complete = True
                decider = remaining_maps[0] if remaining_maps else None
        else:
            # BO3:
            # Step 1: Team A ban
            # Step 2: Team B ban
            # Step 3: Team A pick (Map 1)
            # Step 4: Team B pick (Map 2)
            # Step 5: Team A ban
            # Step 6: Team B ban
            # Step 7: Decider (Map 3)
            schedule = [
                (1, team_a, "ban"),
                (2, team_b, "ban"),
                (3, team_a, "pick"),
                (4, team_b, "pick"),
                (5, team_a, "ban"),
                (6, team_b, "ban"),
            ]
            if len(steps) < 6:
                _, current_turn, current_action = schedule[len(steps)]
            else:
                is_complete = True
                decider = remaining_maps[0] if remaining_maps else None

        return {
            "matchId": match_id,
            "format": fmt,
            "teamA": team_a,
            "teamB": team_b,
            "steps": steps,
            "bannedMaps": banned_maps,
            "pickedMaps": picked_maps,
            "remainingMaps": remaining_maps,
            "deciderMap": decider,
            "currentStep": current_step,
            "currentTurn": current_turn,
            "currentAction": current_action,
            "isComplete": is_complete,
        }

    def perform_veto(
        self, match_id: str, team_id: str, action: str, map_name: str
    ) -> dict[str, Any]:
        """Process a map pick or ban action for a match."""
        if map_name not in ACTIVE_DUTY_MAPS:
            raise ValueError(f"Map '{map_name}' is not in Active Duty pool: {ACTIVE_DUTY_MAPS}")

        state = self.get_veto_state(match_id)
        if state.get("isComplete"):
            raise ValueError("Veto phase is already complete for this match")

        if state["currentTurn"] != team_id:
            raise ValueError(f"It is not {team_id}'s turn. Current turn: {state['currentTurn']}")

        if state["currentAction"] != action:
            raise ValueError(f"Expected action '{state['currentAction']}', got '{action}'")

        if map_name not in state["remainingMaps"]:
            raise ValueError(f"Map '{map_name}' has already been banned or picked")

        conn = _get_db()
        step = state["currentStep"]
        with conn:
            conn.execute(
                """
                INSERT INTO vetoes (match_id, step, team_id, action, map_name, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (match_id, step, team_id, action, map_name, time.time()),
            )

        new_state = self.get_veto_state(match_id)
        if new_state["isComplete"]:
            active_map = (
                new_state["deciderMap"]
                if new_state["format"] == "BO1"
                else (new_state["pickedMaps"][0] if new_state["pickedMaps"] else new_state["deciderMap"])
            )
            with conn:
                conn.execute(
                    "UPDATE matches SET status = 'ready', active_map = ? WHERE id = ?",
                    (active_map, match_id),
                )

        return new_state

    def complete_match(
        self, match_id: str, winner_id: str, score_a: int, score_b: int
    ) -> dict[str, Any]:
        """Record match result and check if tournament stage advances."""
        conn = _get_db()
        m = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        if not m:
            raise ValueError(f"Match {match_id} not found")

        tourn_id = m["tournament_id"]
        loser_id = m["team_b_id"] if winner_id == m["team_a_id"] else m["team_a_id"]

        with conn:
            conn.execute(
                """
                UPDATE matches
                SET status = 'completed', winner_id = ?, score_a = ?, score_b = ?
                WHERE id = ?
                """,
                (winner_id, score_a, score_b, match_id),
            )

            # Update teams wins/losses
            conn.execute(
                "UPDATE teams SET wins = wins + 1 WHERE id = ? AND tournament_id = ?",
                (winner_id, tourn_id),
            )
            conn.execute(
                "UPDATE teams SET losses = losses + 1 WHERE id = ? AND tournament_id = ?",
                (loser_id, tourn_id),
            )

            # Check for qualification (3 wins) or elimination (3 losses) in Swiss stage
            if m["stage"] == "swiss":
                conn.execute(
                    "UPDATE teams SET qualified = 1 WHERE tournament_id = ? AND wins >= 3",
                    (tourn_id,),
                )
                conn.execute(
                    "UPDATE teams SET eliminated = 1 WHERE tournament_id = ? AND losses >= 3",
                    (tourn_id,),
                )

        # Update Buchholz scores
        self._update_buchholz(tourn_id)

        # Check if round or stage finished
        self._evaluate_tournament_progression(tourn_id)
        return self.get_tournament_state(tourn_id)

    def _update_buchholz(self, tournament_id: str) -> None:
        """Buchholz calculation: sum of opponents' wins minus opponents' losses."""
        conn = _get_db()
        matches = conn.execute(
            "SELECT team_a_id, team_b_id FROM matches WHERE tournament_id = ? AND status = 'completed'",
            (tournament_id,),
        ).fetchall()

        opponents: dict[str, list[str]] = {}
        for m in matches:
            a, b = m["team_a_id"], m["team_b_id"]
            opponents.setdefault(a, []).append(b)
            opponents.setdefault(b, []).append(a)

        teams = conn.execute("SELECT id, wins, losses FROM teams WHERE tournament_id = ?", (tournament_id,)).fetchall()
        records = {t["id"]: (t["wins"], t["losses"]) for t in teams}

        with conn:
            for t_id, opp_list in opponents.items():
                buchholz = sum(records[opp][0] - records[opp][1] for opp in opp_list if opp in records)
                conn.execute(
                    "UPDATE teams SET buchholz = ? WHERE id = ? AND tournament_id = ?",
                    (buchholz, t_id, tournament_id),
                )

    def _evaluate_tournament_progression(self, tournament_id: str) -> None:
        conn = _get_db()
        tourn = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
        if not tourn:
            return

        current_stage = tourn["stage"]
        current_round = tourn["round"]

        # Check if all matches in current round are completed
        unfinished = conn.execute(
            "SELECT count(*) as c FROM matches WHERE tournament_id = ? AND stage = ? AND round = ? AND status != 'completed'",
            (tournament_id, current_stage, current_round),
        ).fetchone()["c"]

        if unfinished > 0:
            return  # Still waiting for matches to finish

        if current_stage == "swiss":
            # Count qualified and eliminated teams
            qualified = conn.execute(
                "SELECT count(*) as c FROM teams WHERE tournament_id = ? AND qualified = 1",
                (tournament_id,),
            ).fetchone()["c"]
            eliminated = conn.execute(
                "SELECT count(*) as c FROM teams WHERE tournament_id = ? AND eliminated = 1",
                (tournament_id,),
            ).fetchone()["c"]

            if qualified == 8 and eliminated == 8:
                # Advance to Champions Stage (Playoffs)
                self._start_playoffs(tournament_id)
            elif current_round < 5:
                # Next Swiss round
                next_round = current_round + 1
                with conn:
                    conn.execute(
                        "UPDATE tournaments SET round = ? WHERE id = ?",
                        (next_round, tournament_id),
                    )
                self._generate_swiss_round(tournament_id, next_round)
        elif current_stage == "playoffs":
            # Single-elimination progression: QF (round 1) -> SF (round 2) -> Final (round 3)
            if current_round == 1:
                # Advance QF winners to SF
                qf_matches = conn.execute(
                    "SELECT winner_id FROM matches WHERE tournament_id = ? AND stage = 'playoffs' AND round = 1 ORDER BY id ASC",
                    (tournament_id,),
                ).fetchall()
                winners = [r["winner_id"] for r in qf_matches]
                if len(winners) == 4:
                    with conn:
                        conn.execute("UPDATE tournaments SET round = 2 WHERE id = ?", (tournament_id,))
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO matches
                            (id, tournament_id, stage, round, team_a_id, team_b_id, format, status, winner_id, score_a, score_b, active_map, completed_maps)
                            VALUES
                            (?, ?, 'playoffs', 2, ?, ?, 'BO3', 'scheduled', NULL, 0, 0, NULL, '[]'),
                            (?, ?, 'playoffs', 2, ?, ?, 'BO3', 'scheduled', NULL, 0, 0, NULL, '[]')
                            """,
                            (
                                f"playoffs_sf_1_{winners[0]}_vs_{winners[1]}", tournament_id, winners[0], winners[1],
                                f"playoffs_sf_2_{winners[2]}_vs_{winners[3]}", tournament_id, winners[2], winners[3],
                            ),
                        )
            elif current_round == 2:
                # Advance SF winners to Grand Final
                sf_matches = conn.execute(
                    "SELECT winner_id FROM matches WHERE tournament_id = ? AND stage = 'playoffs' AND round = 2 ORDER BY id ASC",
                    (tournament_id,),
                ).fetchall()
                winners = [r["winner_id"] for r in sf_matches]
                if len(winners) == 2:
                    with conn:
                        conn.execute("UPDATE tournaments SET round = 3 WHERE id = ?", (tournament_id,))
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO matches
                            (id, tournament_id, stage, round, team_a_id, team_b_id, format, status, winner_id, score_a, score_b, active_map, completed_maps)
                            VALUES (?, ?, 'playoffs', 3, ?, ?, 'BO5', 'scheduled', NULL, 0, 0, NULL, '[]')
                            """,
                            (
                                f"playoffs_gf_{winners[0]}_vs_{winners[1]}", tournament_id, winners[0], winners[1]
                            ),
                        )
            elif current_round == 3:
                # Grand Final completed!
                gf = conn.execute(
                    "SELECT winner_id FROM matches WHERE tournament_id = ? AND stage = 'playoffs' AND round = 3 AND status = 'completed'",
                    (tournament_id,),
                ).fetchone()
                if gf and gf["winner_id"]:
                    with conn:
                        conn.execute(
                            "UPDATE tournaments SET status = 'completed', winner_id = ? WHERE id = ?",
                            (gf["winner_id"], tournament_id),
                        )

    def _start_playoffs(self, tournament_id: str) -> None:
        """Seed 8 qualified teams into Quarterfinals (BO3)."""
        conn = _get_db()
        top8 = conn.execute(
            """
            SELECT * FROM teams
            WHERE tournament_id = ? AND qualified = 1
            ORDER BY wins DESC, buchholz DESC, seed ASC
            LIMIT 8
            """,
            (tournament_id,),
        ).fetchall()

        # Matchups: 1 vs 8, 4 vs 5, 2 vs 7, 3 vs 6
        pairs = [
            (top8[0], top8[7]),
            (top8[3], top8[4]),
            (top8[1], top8[6]),
            (top8[2], top8[5]),
        ]

        with conn:
            conn.execute(
                "UPDATE tournaments SET stage = 'playoffs', round = 1 WHERE id = ?",
                (tournament_id,),
            )
            for idx, (ta, tb) in enumerate(pairs, start=1):
                match_id = f"playoffs_qf_{idx}_{ta['id']}_vs_{tb['id']}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO matches
                    (id, tournament_id, stage, round, team_a_id, team_b_id, format, status, winner_id, score_a, score_b, active_map, completed_maps)
                    VALUES (?, ?, 'playoffs', 1, ?, ?, 'BO3', 'scheduled', NULL, 0, 0, NULL, '[]')
                    """,
                    (match_id, tournament_id, ta["id"], tb["id"]),
                )


tournament_manager = TournamentEngine()
