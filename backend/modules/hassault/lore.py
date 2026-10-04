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
The Deadzone is what the grid left behind: relay towns in the sand, a reactor \
nobody pays to cool, a reserve bank still guarding the credits, office towers with \
the meetings still booked. The machines in them did not stop. The invoices did.

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

    Keyed by map name: one for every map this repo ships, which a test enforces.
    """

    map_name: str
    #: The place, in-fiction. The map's own `title` stays what it is — this is the
    #: name on the door, not a rename.
    site: str
    tagline: str
    brief: str


MAP_BRIEFS: dict[str, MapBrief] = {
    "hd_dust2": MapBrief(
        map_name="hd_dust2",
        site="Citadel Relay, Dust Basin",
        tagline="The relay still answers. Nobody remembers the question.",
        brief=(
            "A walled market town grown up around a relay mast in the sand. Long A is "
            "a rifle lane in full sun, the tunnels are the only shade, and both sites "
            "sit where the old uplink cables still come up through the paving."
        ),
    ),
    "hd_mirage": MapBrief(
        map_name="hd_mirage",
        site="Mirage Exchange",
        tagline="The courtyard trades in sightlines.",
        brief=(
            "A palace exchange where compute was auctioned by the hour. The courtyard "
            "is open to every window around it, and whoever holds mid decides which "
            "site the other side gets to walk into."
        ),
    ),
    "hd_inferno": MapBrief(
        map_name="hd_inferno",
        site="Inferno Terraces",
        tagline="Every street is a corridor and every corridor is watched.",
        brief=(
            "A hill town that ran its own small grid off a bank of rooftop panels. "
            "The streets are narrow enough to touch both walls. Banana is the one way "
            "up to B, and both sides know it."
        ),
    ),
    "hd_nuke": MapBrief(
        map_name="hd_nuke",
        site="Coolant Plant 4",
        tagline="Two sites, one above the other, and a floor between them.",
        brief=(
            "A reactor that kept the grid warm until the grid stopped paying for it. "
            "The yard is open ground, the halls are not, and the vents connect things "
            "the floor plan says should be apart."
        ),
    ),
    "hd_office": MapBrief(
        map_name="hd_office",
        site="Tower 9, Floor 31",
        tagline="The meeting rooms are still booked.",
        brief=(
            "A high-rise floor where the orchestration was signed off. Cubicles, "
            "glass and a server core, all of it close enough that the fight is "
            "decided by who opens the door."
        ),
    ),
    "hd_bank": MapBrief(
        map_name="hd_bank",
        site="Compute Reserve Bank",
        tagline="The vault holds the only currency left.",
        brief=(
            "The reserve that backed the grid's compute credits, with the ledgers "
            "still in the vault. In through the lobby, through the offices, down to "
            "the gold. HALON guards it and ARC wants it."
        ),
    ),
    "hd_assault": MapBrief(
        map_name="hd_assault",
        site="Depot 12",
        tagline="Somebody is still shipping something.",
        brief=(
            "A freight depot where containers keep arriving on a schedule nobody "
            "wrote. One side holds the warehouse and the racks, the other comes "
            "across the yard from the rail line."
        ),
    ),
    "hd_facility": MapBrief(
        map_name="hd_facility",
        site="Research Facility Kappa",
        tagline="Emergency lighting only.",
        brief=(
            "The lab complex where the shells were tested, now running on emergency "
            "power. Two corridors, a coolant pit between them, and alarms that never "
            "learned to stop."
        ),
    ),
    "hd_junkflea": MapBrief(
        map_name="hd_junkflea",
        site="Junk Flea Yard",
        tagline="Everything here was something else first.",
        brief=(
            "A scrapyard market built out of what the grid threw away. Bale walls, "
            "tyre stacks and a bridge over the middle. There is cover everywhere, and "
            "none of it is where you expect."
        ),
    ),
}


def faction_for_team(team: int) -> Faction:
    """The faction a `playerstart`'s team index belongs to.

    Out-of-range indices fold onto the first faction rather than raising: a map is
    data (a bundled one *or* a designer draft), and an unexpected team
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
