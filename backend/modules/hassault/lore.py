"""The Deadzone: HorribleAssault's setting, as data rather than prose.

Fiction is load-bearing here. The faction palette is what the avatars are tinted
with, the rank names are what the ranked ladder puts on a card, and the map briefs
are what the menu shows before a match. Three surfaces reading three copies of the
same fiction is three places for it to drift, so it lives here once and is
**served** — the `plane_order` / `zoom_levels` precedent that already governs every
other number both clients need.

It also replaces something that was not ours. The two team colours were commented
"CLA sand, RVSF blue" and the constants in the native client were named after the
same pair: AssaultCube's factions. The maps are painted from our own JSON brushes
and the gunshots are synthesized rather than sampled for one reason, and the
setting gets the same treatment.

Two things this module deliberately does **not** own:

- **The ladder.** Tiers, floors and ratings belong to the game server
  (`backend/games_server/store.py::TIERS`) because a node cannot adjudicate its own
  player. `RANKS` is a *naming layer* keyed by those tier ids — renaming a rank must
  never be able to move a rating.
- **The team split.** Which spawn belongs to which side is in the map's
  `playerstart` entities, as `team: 0` / `team: 1`. `TEAM_FACTIONS` maps that index
  onto a faction; it does not decide it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The premise, in the two sentences a loading screen has room for.
PREMISE = (
    "Autonomous orchestration ran the grid until it stopped being asked to. What is "
    "left are the shells — and compute is the only currency that survived."
)

LONG_PREMISE = """\
The Deadzone is what the grid left behind: coolant pits drained to bedrock, \
switchyards still humming off dead schedules, campus atria with the lights on and \
nobody billed. The machines in them did not stop. The invoices did.

ARC says a machine left running belongs to whoever keeps it running. HALON says it \
belongs to the contract, and the contract was never cancelled. Neither side is \
right. The maps are the argument.\
"""


@dataclass(frozen=True, slots=True)
class Faction:
    """One side, and everything a renderer or a menu needs to draw it.

    `primary` is the colour a *body* is tinted with, and it carries a constraint
    that outlives any restyle: a body has to be findable against a wall of any hue,
    which is why these are not drawn from the world's texture-id palette. Changing
    one to something the architecture generator can also produce is a legibility
    regression, not a taste question.
    """

    id: str
    name: str
    #: The four-or-five-letter form that fits on a HUD and a scoreboard column.
    short: str
    motto: str
    blurb: str
    #: Body tint. Hex, because both a CSS rule and a wgpu vertex colour start here.
    primary: str
    #: Trim: insignia strokes, UI accents, the stripe on a nameplate.
    secondary: str
    #: Procedural insignia: a shape id both clients can draw with no asset to ship.
    #: Deliberately not an image — an SVG or a PNG is a file to bundle, and the same
    #: rule that keeps AssaultCube's media out keeps ours generated.
    insignia: str
    #: Callsigns are generated, never stored: a bot needs a name, and so does a
    #: player who has not claimed a handle. Prefix + number is enough character.
    callsigns: tuple[str, ...] = field(default=())


FACTIONS: dict[str, Faction] = {
    "arc": Faction(
        id="arc",
        name="Assembly of Reclaimed Compute",
        short="ARC",
        motto="It runs. It's ours.",
        blurb=(
            "Squatters, operators, and people who think a machine left running "
            "belongs to whoever keeps it running. Scavenged kit, mismatched plates, "
            "unit marks stencilled by hand."
        ),
        primary="#d9a441",
        secondary="#f2e2c4",
        insignia="chevron-open",
        callsigns=("SCRAP", "TALLY", "EMBER", "RUST", "KILN", "DRIFT"),
    ),
    "halon": Faction(
        id="halon",
        name="HALON Custodial Systems",
        short="HALON",
        motto="The contract stands.",
        blurb=(
            "The custodial contractor that never got the cancellation notice. Still "
            "issued, still uniform, still auditing a site whose owner has not "
            "existed for years."
        ),
        primary="#4c8fd4",
        secondary="#cfe0f2",
        insignia="hex-lock",
        callsigns=("WARD", "AUDIT", "CLAUSE", "TENURE", "SEAL", "REMIT"),
    ),
}

#: Team index → faction. The index comes from the map's `playerstart` entities;
#: this only says which fiction is painted over it. Order matches the colours the
#: clients already used for team 0 and team 1, so no existing map changes meaning.
TEAM_FACTIONS: tuple[str, str] = ("arc", "halon")

#: Ladder tier id → the name HorribleAssault shows for it.
#:
#: Keyed by `backend/games_server/store.py::TIERS`, which is the authority on what
#: tiers exist and what they are worth. A tier with no entry here falls back to its
#: raw id rather than vanishing, because a missing rank name must not be able to
#: hide a rated player from the ladder.
RANKS: dict[str, str] = {
    "bronze": "Scavenger",
    "silver": "Runner",
    "gold": "Operator",
    "platinum": "Breaker",
    "diamond": "Warden",
    "master": "Overseer",
    "grandmaster": "Architect",
}


def rank_name(tier: str) -> str:
    """The display name for a ladder tier, falling back to the tier id itself."""
    return RANKS.get(tier, tier)


@dataclass(frozen=True, slots=True)
class MapBrief:
    """What the menu and the loading screen say about a map.

    Keyed by map name, and **only** for the maps this repo ships. A brief for a map
    read out of somebody's AssaultCube install would be us writing fiction over
    their level, which is the same mistake as bundling it.
    """

    map_name: str
    #: The place, in-fiction. The map's own `title` stays what it is — this is the
    #: name on the door, not a rename.
    site: str
    tagline: str
    brief: str


MAP_BRIEFS: dict[str, MapBrief] = {
    "hd_pit": MapBrief(
        map_name="hd_pit",
        site="Coolant Pit 7",
        tagline="Drained to bedrock. Nothing to hide behind but the pumps.",
        brief=(
            "They pulled the coolant out in a week and never came back for the "
            "plant. What is left is a bowl with a floor you can see across and a rim "
            "you cannot hold — every angle is somebody else's angle."
        ),
    ),
    "hd_crossing": MapBrief(
        map_name="hd_crossing",
        site="Switchyard Crossing",
        tagline="Still energised. Still on a schedule nobody wrote.",
        brief=(
            "Two approaches, one yard, and a bus bar down the middle neither side "
            "can walk through. The gear still trips on a timer. Nobody has found the "
            "timer."
        ),
    ),
    "hd_atrium": MapBrief(
        map_name="hd_atrium",
        site="Flagship Campus, North Atrium",
        tagline="The lights are on. The invoice is not.",
        brief=(
            "A lobby built to be photographed, with a gallery ring above it and "
            "sightlines that were never meant to be defended. HALON still logs "
            "entries here. ARC still uses the service stairs."
        ),
    ),
}


def faction_for_team(team: int) -> Faction:
    """The faction a `playerstart`'s team index belongs to.

    Out-of-range indices fold onto the first faction rather than raising: a map is
    data (a bundled one *or* one out of somebody's install), and an unexpected team
    number is a reason to draw somebody in amber, not to fail the match.
    """
    return FACTIONS[TEAM_FACTIONS[team % len(TEAM_FACTIONS)]]


def brief_for(map_name: str) -> MapBrief | None:
    """The brief for a map, or `None` for one we did not write."""
    return MAP_BRIEFS.get(map_name)


def to_dict() -> dict[str, Any]:
    """The whole setting, in the shape both clients read it in."""
    return {
        "premise": PREMISE,
        "longPremise": LONG_PREMISE,
        "factions": [
            {
                "id": f.id,
                "name": f.name,
                "short": f.short,
                "motto": f.motto,
                "blurb": f.blurb,
                "primary": f.primary,
                "secondary": f.secondary,
                "insignia": f.insignia,
                "callsigns": list(f.callsigns),
            }
            for f in (FACTIONS[fid] for fid in TEAM_FACTIONS)
        ],
        "teamFactions": list(TEAM_FACTIONS),
        "ranks": dict(RANKS),
        "mapBriefs": {
            b.map_name: {
                "mapName": b.map_name,
                "site": b.site,
                "tagline": b.tagline,
                "brief": b.brief,
            }
            for b in MAP_BRIEFS.values()
        },
    }
