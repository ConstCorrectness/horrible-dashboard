"""Co-op PvE Operations: Breach & Clear and Hostage Rescue Mode.

Tactical cooperative operation where human players and squad AI bots breach fortified
strongholds, neutralize hostile defender bots, escort hostages to safety extraction
zones, and coordinate using the tactical squad command wheel.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from .base import GameMode

if TYPE_CHECKING:
    from ..match import Command, MatchPlayer, MatchRoom


@dataclass
class Hostage:
    id: str
    name: str
    x: float
    y: float
    z: float
    hp: int = 100
    carrier_id: Optional[str] = None
    rescued: bool = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "z": round(self.z, 2),
            "hp": self.hp,
            "carrierId": self.carrier_id,
            "rescued": self.rescued,
        }


class Breach(GameMode):
    """Breach & Clear: Co-op PvE tactical hostage rescue and room clearing."""

    id: str = "breach"
    name: str = "Breach & Clear (Co-op PvE)"
    teams: bool = True
    score_label: str = "Rescued"
    version: int = 1

    def __init__(self) -> None:
        self.hostages: list[Hostage] = []
        self.extraction_zone: tuple[float, float, float] = (34.0, 56.0, 0.0)
        self.extraction_radius: float = 6.0
        self.operation_time: float = 300.0  # 5 minutes
        self.time_remaining: float = 300.0
        self.mission_cleared: bool = False
        self.stars_awarded: int = 0
        self.hostages_rescued_count: int = 0
        self.squad_commands: dict[str, dict[str, Any]] = {}

    def attach(self, room: MatchRoom) -> None:
        """Seed default hostages and extraction zone based on map."""
        self.hostages = [
            Hostage(id="hostage_1", name="Scientist Adams", x=48.0, y=38.0, z=2.8),
            Hostage(id="hostage_2", name="Ambassador Chen", x=16.0, y=44.0, z=0.8),
        ]
        # Set CT spawn area as extraction zone
        if room.spawns:
            ct_spawns = [s for s in room.spawns if s.team == 0]
            if ct_spawns:
                s0 = ct_spawns[0]
                self.extraction_zone = (s0.x, s0.y, s0.z)

    def reset(self, room: MatchRoom) -> None:
        self.attach(room)
        self.time_remaining = self.operation_time
        self.mission_cleared = False
        self.stars_awarded = 0
        self.hostages_rescued_count = 0
        self.squad_commands.clear()
        room.scores = [0, 0]

    def tick(self, room: MatchRoom, elapsed: float, now: float) -> None:
        """Advance operation timer and evaluate hostage rescue / extraction."""
        if self.mission_cleared:
            return

        self.time_remaining = max(0.0, self.time_remaining - elapsed)
        ex_x, ex_y, _ = self.extraction_zone

        for h in self.hostages:
            if h.rescued:
                continue

            # If carried by a player, update position to follow player
            if h.carrier_id:
                carrier = room.players.get(h.carrier_id)
                if carrier and getattr(carrier, "alive", True) and carrier.state.health > 0:
                    # Hostage follows 1.5m behind carrier
                    rad = math.radians(carrier.state.yaw)
                    h.x = carrier.state.x - math.cos(rad) * 1.5
                    h.y = carrier.state.y - math.sin(rad) * 1.5
                    h.z = carrier.state.z

                    # Check if reached extraction zone
                    dist_to_ex = math.hypot(carrier.state.x - ex_x, carrier.state.y - ex_y)
                    if dist_to_ex <= self.extraction_radius:
                        h.rescued = True
                        h.carrier_id = None
                        self.hostages_rescued_count += 1
                        room.scores[0] = self.hostages_rescued_count
                        room.fx.append({
                            "type": "hostage_rescued",
                            "hostage": h.name,
                            "rescuer": carrier.id,
                        })
                else:
                    # Carrier died or disconnected; drop hostage at spot
                    h.carrier_id = None

        # Check mission completion
        if all(h.rescued for h in self.hostages) and len(self.hostages) > 0:
            self.mission_cleared = True
            time_spent = self.operation_time - self.time_remaining
            if time_spent < 90.0:
                self.stars_awarded = 3
            elif time_spent < 180.0:
                self.stars_awarded = 2
            else:
                self.stars_awarded = 1

    def on_command(self, room: MatchRoom, player: MatchPlayer, command: Command) -> None:
        """Handle interaction (press Use/E near hostage to grab or release)."""
        if not getattr(player, "alive", True) or player.team != 0:
            return

        # Check if player presses use key to interact with nearby hostage
        if getattr(command, "use", False):
            px, py = player.state.x, player.state.y
            for h in self.hostages:
                if h.rescued:
                    continue
                dist = math.hypot(h.x - px, h.y - py)
                if dist <= 2.8:
                    if h.carrier_id is None:
                        h.carrier_id = player.id
                    elif h.carrier_id == player.id:
                        h.carrier_id = None
                    break

    def issue_squad_command(
        self,
        room: MatchRoom,
        caller: MatchPlayer,
        command_type: str,
        target_pos: Optional[tuple[float, float, float]] = None,
    ) -> dict[str, Any]:
        """Broadcast squad command to bot squadmates on caller's team."""
        cmd_record = {
            "callerId": caller.id,
            "callerTeam": caller.team,
            "type": command_type.upper(),  # HOLD, BREACH, SMOKE, COVER, REGROUP
            "target": target_pos or (caller.state.x, caller.state.y, caller.state.z),
            "timestamp": time.time(),
        }
        self.squad_commands[caller.id] = cmd_record

        # Direct allied bots to carry out squad command
        for p in room.players.values():
            if getattr(p, "is_bot", False) and p.team == caller.team and p.id != caller.id:
                setattr(p, "squad_command", cmd_record)

        room.fx.append({
            "type": "squad_command",
            "caller": caller.id,
            "command": command_type.upper(),
            "target": cmd_record["target"],
        })
        return cmd_record

    def welcome_state(self, room: MatchRoom) -> dict[str, Any]:
        return {
            "mode": self.id,
            "teams": self.teams,
            "extractionZone": list(self.extraction_zone),
            "extractionRadius": self.extraction_radius,
            "totalHostages": len(self.hostages),
        }

    def shared_state(self, room: MatchRoom) -> dict[str, Any]:
        return {
            "timeRemaining": round(self.time_remaining, 1),
            "missionCleared": self.mission_cleared,
            "stars": self.stars_awarded,
            "hostages": [h.snapshot() for h in self.hostages],
            "rescuedCount": self.hostages_rescued_count,
        }

    def private_state(self, room: MatchRoom, player: MatchPlayer) -> dict[str, Any]:
        carrying = [h.id for h in self.hostages if h.carrier_id == player.id]
        return {
            "carryingHostage": len(carrying) > 0,
            "hostageId": carrying[0] if carrying else None,
            "activeCommand": self.squad_commands.get(player.id),
        }
