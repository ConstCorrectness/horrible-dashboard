"""The body a shot is resolved against — served, versioned, and deliberately not final.

Every number in here used to be a constant in `physics.py` copied by hand into
`world.ts`, `player.ts`, `avatars.ts` and `bodies.rs`. Four copies of a figure
nobody has finished choosing: there is no wall penetration yet, the head band has
never been checked against a real character model, and the relationship between
what a body *looks* like and what it *is* has only ever been asserted. So this
module exists to make changing the hitbox cheap and observable rather than
frightening, and the clients read it instead of holding their own copy — the
`plane_order` / `zoom_levels` precedent.

**The spec is the authority; a mesh never is.** An avatar is fitted to this, not
the other way round, and no renderer gets to widen a body by drawing it wide. That
much is fixed even though the numbers are not.

## The version is a hash, not a label

`spec_id` is derived from the numbers themselves. That is the whole point: a
hand-maintained revision is a thing you forget to bump, and the failure mode of
forgetting is the worst one available here — the Python simulation and the two
client implementations replaying a fixture that no longer describes any of them,
agreeing with each other about a body that does not exist. A content hash cannot
be forgotten. Change a number, the id changes, and
`physics-vectors.json` fails until it is regenerated deliberately.

## What is *not* here

- **Step height, speeds, gravity, friction.** Those are movement, not the body.
  They stay in `physics.py`.
- **Wall penetration.** A bullet stopping at a wall is a property of the *wall*
  (`weapons.py` + the world's materials), and the temptation to compensate for a
  missing penetration model by widening a body is exactly what this module exists
  to make visible.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True, slots=True)
class HitboxSpec:
    """One body, in cubes.

    Only `cylinder` exists today. `shape` is carried anyway so that growing a
    capsule, or a segment list finer than the current head/body split, is a value
    change rather than a schema change — and so a client can refuse a shape it does
    not know how to draw instead of drawing the wrong one silently.
    """

    #: Radius of the collision cylinder. Also what the movement code keeps clear,
    #: which is why a body is 2.2 cubes wide and needs 3 cells of clearance.
    radius: float
    #: Standing eye height: where the camera sits and where a shot leaves from.
    eye_height: float
    #: How much body there is above the eye. Standing height is the sum.
    above_eye: float
    #: Crouching multiplies the *eye* height by this; `above_eye` is unchanged, so
    #: a crouched body is shorter by `eye_height * (1 - crouch_eye_scale)`.
    crouch_eye_scale: float
    #: The top band of the body that takes the weapon's head multiplier. A band
    #: measured down from the top rather than an absolute height, because a band
    #: pinned to a standing figure sits above a crouched one entirely and makes
    #: crouching an accidental headshot immunity.
    head_band: float
    #: How far outside the cylinder an avatar mesh may reach before its fit report
    #: is a failure. Non-zero on purpose: cloth, packs and a rifle silhouette are
    #: allowed to exceed the body precisely because the server never asks the mesh
    #: anything. Tightening this is how you make avatars honest; it lives here so
    #: it is tuned alongside the body it is a tolerance on.
    fit_tolerance: float = 0.35
    #: How far the rig's eye bone may sit from `eye_height`. Tighter than
    #: `fit_tolerance` because the eye is not decoration — a first-person camera
    #: and a third-person head are supposed to be the same place.
    eye_tolerance: float = 0.15
    shape: str = "cylinder"

    # -- derived ---------------------------------------------------------------

    @property
    def standing_height(self) -> float:
        return self.eye_height + self.above_eye

    @property
    def crouch_eye_height(self) -> float:
        return self.eye_height * self.crouch_eye_scale

    @property
    def crouch_height(self) -> float:
        return self.crouch_eye_height + self.above_eye

    @property
    def crouch_scale(self) -> float:
        """Crouched height as a fraction of standing — what an avatar is squashed
        by. Derived rather than stored so it cannot disagree with the heights."""
        return self.crouch_height / self.standing_height

    def height_at(self, crouch: float) -> float:
        """Body height mid-crouch, `crouch` being the 0..1 animation fraction."""
        return (
            self.standing_height + (self.crouch_height - self.standing_height) * crouch
        )

    def eye_at(self, crouch: float) -> float:
        """Eye height mid-crouch."""
        return self.eye_height + (self.crouch_eye_height - self.eye_height) * crouch

    # -- identity --------------------------------------------------------------

    def _canonical(self) -> str:
        """The numbers that decide a hit, in a stable textual form.

        `fit_tolerance` and `eye_tolerance` are **excluded**: they govern whether an
        art asset is acceptable, not where a bullet lands, so tightening them must
        not invalidate a physics fixture that is still perfectly accurate.
        """
        return json.dumps(
            {
                "shape": self.shape,
                "radius": self.radius,
                "eye_height": self.eye_height,
                "above_eye": self.above_eye,
                "crouch_eye_scale": self.crouch_eye_scale,
                "head_band": self.head_band,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def spec_id(self) -> str:
        """Content hash of the hit-deciding numbers. Stamped into
        `physics-vectors.json`; a fixture carrying a different one is stale."""
        return hashlib.sha256(self._canonical().encode()).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        """The shape both clients read. Derived values are included rather than
        left for each client to recompute — three implementations of
        `crouch_height` is three chances to round it differently."""
        return {
            "specId": self.spec_id,
            "shape": self.shape,
            "radius": self.radius,
            "eyeHeight": self.eye_height,
            "aboveEye": self.above_eye,
            "standingHeight": self.standing_height,
            "crouchEyeScale": self.crouch_eye_scale,
            "crouchEyeHeight": self.crouch_eye_height,
            "crouchHeight": self.crouch_height,
            "crouchScale": self.crouch_scale,
            "headBand": self.head_band,
            "fitTolerance": self.fit_tolerance,
            "eyeTolerance": self.eye_tolerance,
        }


#: The shipped body. AssaultCube's `entity.h` defaults, which is where the numbers
#: came from and not an argument that they are right for this game.
DEFAULT = HitboxSpec(
    radius=1.1,
    eye_height=4.5,
    above_eye=0.7,
    crouch_eye_scale=0.75,
    head_band=1.0,
)

_override: HitboxSpec | None = None


def current() -> HitboxSpec:
    """The spec in force. Process-global for the same reason the karaoke session
    is: the match server, the REST surface and the agent tools must not be able to
    disagree about how tall a player is, and none of them owns the others."""
    return _override or DEFAULT


def set_override(spec: HitboxSpec | None) -> HitboxSpec:
    """Replace the live spec (the tuning lab) or clear it with `None`.

    Deliberately **not** a setting. `SettingValue` is a scalar and
    `GET /api/settings` hands the whole bag to every plugin; more to the point a
    hitbox is one coherent object, and a half-applied body — a new radius against
    an old head band — is a state no code here should ever have to consider.
    """
    global _override
    _override = spec
    return current()


def tune(**changes: float) -> HitboxSpec:
    """Override a few fields of the current spec, keeping the rest."""
    return set_override(replace(current(), **changes))


def reset() -> HitboxSpec:
    """Back to the shipped body."""
    return set_override(None)
