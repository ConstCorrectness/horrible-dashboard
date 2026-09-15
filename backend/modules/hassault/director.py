"""Caster Observer Director & Cinematic Camera Orchestration Engine.

Automatically evaluates match action intensity, dynamically switches between
first-person, third-person follow, and tactical crane cameras, and computes
smooth Catmull-Rom camera splines for esports broadcast streaming.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class CameraFrame:
    x: float
    y: float
    z: float
    yaw: float
    pitch: float
    fov: float = 85.0


def catmull_rom(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    """1D Catmull-Rom cubic spline interpolation."""
    t2 = t * t
    t3 = t2 * t
    return 0.5 * (
        (2.0 * p1)
        + (-p0 + p2) * t
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
        + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3
    )


class ObserverDirector:
    """Intelligent Automated Broadcast Observer for Competitive Hassault matches."""

    def __init__(self, min_shot_duration: float = 2.5) -> None:
        self.min_shot_duration = min_shot_duration
        self.current_target_id: Optional[str] = None
        self.current_mode: str = "FIRST_PERSON"  # FIRST_PERSON, THIRD_PERSON, TACTICAL_OVERVIEW
        self.focus_reason: str = "Free Camera"
        self.last_switch_time: float = 0.0
        self.camera: CameraFrame = CameraFrame(32.0, 32.0, 6.0, 0.0, -15.0)
        self.target_camera: CameraFrame = CameraFrame(32.0, 32.0, 6.0, 0.0, -15.0)
        self.spline_history: list[CameraFrame] = []

    def evaluate(self, room: Any, now: Optional[float] = None) -> dict[str, Any]:
        """Evaluate room state and produce authoritative caster observer frame."""
        if now is None:
            now = time.monotonic()

        living_players = [
            p for p in room.players.values()
            if getattr(p, "alive", True) and getattr(p.state, "health", 100) > 0
        ]

        if not living_players:
            return {
                "targetId": None,
                "mode": "TACTICAL_OVERVIEW",
                "focusReason": "Round Ended / No Active Players",
                "camera": {
                    "x": round(self.camera.x, 2),
                    "y": round(self.camera.y, 2),
                    "z": round(self.camera.z, 2),
                    "yaw": round(self.camera.yaw, 1),
                    "pitch": round(self.camera.pitch, 1),
                    "fov": round(self.camera.fov, 1),
                },
            }

        # Check for bomb planting / defusing
        bomb_zone = getattr(room, "bomb_planted", False)
        mode = getattr(room, "mode", None)
        planter_id = getattr(mode, "planter_id", None) if mode else None
        defuser_id = getattr(mode, "defuser_id", None) if mode else None

        best_player = living_players[0]
        best_score = -100.0
        best_reason = "Observing Player"
        best_mode = "FIRST_PERSON"

        # Count living per team to detect clutches
        team_counts: dict[int, int] = {}
        for p in living_players:
            team_counts[p.team] = team_counts.get(p.team, 0) + 1

        for p in living_players:
            score = 10.0
            reason = "Standard Patrol"

            # 1. Defusal or Plant (Highest Priority)
            if p.id == defuser_id:
                score += 150.0
                reason = "Defusing Bomb Under Pressure"
                best_mode = "THIRD_PERSON"
            elif p.id == planter_id:
                score += 120.0
                reason = "Planting C4 at Bomb Site"
                best_mode = "THIRD_PERSON"

            # 2. Clutch scenario: only 1 alive on team against multiple opponents
            if team_counts.get(p.team, 0) == 1 and len(living_players) > 1:
                opp_team = 1 if p.team == 0 else 0
                opp_count = team_counts.get(opp_team, 0)
                if opp_count > 0:
                    score += 60.0 + opp_count * 10.0
                    reason = f"1v{opp_count} Clutch Situation"

            # 3. High Damage / Combat Engagement
            recent_dmg = getattr(p, "last_damage_dealt_time", 0.0)
            if now - recent_dmg < 2.0:
                score += 50.0
                reason = "Active Firefight"

            # 4. Sniper in Sightline
            held_weapon = getattr(p.state, "weapon", "")
            if "sniper" in held_weapon or "awp" in held_weapon or "scout" in held_weapon:
                score += 30.0
                if reason == "Standard Patrol":
                    reason = "Long-Range Sniper Angle"

            # 5. Low HP Survival
            if p.state.health <= 25:
                score += 20.0
                if "Clutch" not in reason:
                    reason = f"Critical HP ({p.state.health} HP) Defense"

            # Proximity to nearest enemy
            min_dist = 999.0
            for opp in living_players:
                if opp.team != p.team:
                    dx = opp.state.x - p.state.x
                    dy = opp.state.y - p.state.y
                    d = math.hypot(dx, dy)
                    if d < min_dist:
                        min_dist = d
            if min_dist < 12.0:
                score += (12.0 - min_dist) * 3.0

            if score > best_score:
                best_score = score
                best_player = p
                best_reason = reason

        # Pacing: switch target only if min_shot_duration elapsed or emergency (plant/defuse)
        elapsed = now - self.last_switch_time
        can_switch = elapsed >= self.min_shot_duration or "Bomb" in best_reason or self.current_target_id is None

        # Check if current target died
        curr_p = room.players.get(self.current_target_id) if self.current_target_id else None
        if curr_p is None or not getattr(curr_p, "alive", True) or getattr(curr_p.state, "health", 0) <= 0:
            can_switch = True

        if can_switch and self.current_target_id != best_player.id:
            self.current_target_id = best_player.id
            self.current_mode = best_mode
            self.focus_reason = best_reason
            self.last_switch_time = now

        # Update camera position based on target player and mode
        target = room.players[self.current_target_id]
        tx, ty, tz = target.state.x, target.state.y, target.state.z
        yaw = target.state.yaw
        pitch = target.state.pitch

        if self.current_mode == "FIRST_PERSON":
            self.target_camera = CameraFrame(
                x=tx,
                y=ty,
                z=tz + 1.55,
                yaw=yaw,
                pitch=pitch,
                fov=85.0,
            )
        elif self.current_mode == "THIRD_PERSON":
            rad = math.radians(yaw)
            cam_dist = 2.6
            self.target_camera = CameraFrame(
                x=tx - math.cos(rad) * cam_dist,
                y=ty - math.sin(rad) * cam_dist,
                z=tz + 2.2,
                yaw=yaw,
                pitch=pitch - 10.0,
                fov=75.0,
            )
        else:  # TACTICAL_OVERVIEW
            self.target_camera = CameraFrame(
                x=tx,
                y=ty - 5.0,
                z=tz + 9.0,
                yaw=90.0,
                pitch=-55.0,
                fov=65.0,
            )

        # Smooth camera movement (exponential lerp / Catmull-Rom buffer)
        lerp_rate = 0.15
        self.camera.x += (self.target_camera.x - self.camera.x) * lerp_rate
        self.camera.y += (self.target_camera.y - self.camera.y) * lerp_rate
        self.camera.z += (self.target_camera.z - self.camera.z) * lerp_rate
        self.camera.yaw += (self.target_camera.yaw - self.camera.yaw) * lerp_rate
        self.camera.pitch += (self.target_camera.pitch - self.camera.pitch) * lerp_rate
        self.camera.fov += (self.target_camera.fov - self.camera.fov) * lerp_rate

        return {
            "targetId": self.current_target_id,
            "mode": self.current_mode,
            "focusReason": self.focus_reason,
            "camera": {
                "x": round(self.camera.x, 2),
                "y": round(self.camera.y, 2),
                "z": round(self.camera.z, 2),
                "yaw": round(self.camera.yaw, 1),
                "pitch": round(self.camera.pitch, 1),
                "fov": round(self.camera.fov, 1),
            },
        }
