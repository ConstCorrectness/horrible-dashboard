"""Downloadable skin packs: how a skin that is not in this repo reaches a player.

`SKIN_CATALOG` in `skins.py` is a Python list, which is the right shape for the
skins the game ships with and the wrong shape for every skin after them. Adding
one meant editing the backend; a player could not have a skin the node's own
source did not name, and there was nowhere for a skin somebody else made to live
at all.

A **pack** is the unit that fixes that: a zip carrying a `pack.json` and its
textures, fetched to the user's own node and unpacked under
`$HORRIBLE_DATA_DIR/hassault/skins/<pack id>/`. Nothing is bundled — the same
rule that keeps AssaultCube's maps on the user's own install, SearXNG
unbundled, and karaoke songs fetched rather than shipped. The catalog the rest
of the module reads becomes *built-ins plus whatever is installed*.

## Rules that are silent if broken

- **A pack may not shadow a built-in.** `hd_*` maps resolve before the
  AssaultCube install for exactly this reason: two catalogs that can each define
  the same id means the same name renders differently depending on what somebody
  installed, and the failure looks like a rendering bug rather than a collision.
  A shadowing skin is dropped and *reported*, never silently preferred.
- **A zip member's path is not to be trusted.** `../../.ssh/authorized_keys` is
  a legal name inside a zip file, and `extractall` will happily write it. Every
  member is resolved against the destination and refused if it lands outside —
  the same check `llamacpp` makes before deleting a file, for the same reason: a
  `..` is how a route becomes an arbitrary-file route.
- **The size budget is checked before extracting, from the declared sizes.**
  A 40 KB zip can declare 40 GB of contents. Checking as you go means finding out
  when the disk is full, which is the llamacpp download's lesson.
- **A pack lands via `.part` and a rename.** A half-extracted directory that is
  scanned as installed is a skin with half its textures, and nothing says so.
- **A digest mismatch aborts and writes nothing.** A pack fetched without one
  installs as `verified: false` rather than pretending it was checked.

## What a pack looks like

```json
{
  "id": "neon-collection",
  "name": "Neon Collection",
  "version": "1.0.0",
  "author": "somebody",
  "skins": [
    {
      "id": "neon_assault_pulse",
      "name": "Pulse",
      "weaponId": "assault",
      "rarity": "covert",
      "collection": "Neon Collection",
      "baseColor": "#22d3ee",
      "accentColor": "#f472b6",
      "patternType": "custom_art",
      "description": "...",
      "texture": "pulse.png"
    }
  ]
}
```

`texture` is optional. Without it the skin renders exactly as a built-in does —
its two colours distributed across the weapon's parts by `patternType` — so a
pack is useful before either client can draw a texture at all.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.paths import data_dir

log = logging.getLogger(__name__)

# The one place a pack's files live. Under the data directory rather than beside
# the source, because `backend/paths.py` is the single authority on where files
# go and a packaged install has no writable source tree.
PACKS_SUBDIR = ("hassault", "skins")

#: How large a pack's download may be, and how much it may unpack to.
#:
#: Two separate caps because they fail differently: the first bounds the network
#: transfer, the second bounds the disk. A skin pack is a manifest and a handful
#: of textures — anything approaching these is not a skin pack.
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
MAX_UNPACKED_BYTES = 96 * 1024 * 1024

#: The most files a pack may contain. A zip of a hundred thousand empty entries
#: costs nothing to declare and a great deal to create.
MAX_MEMBERS = 512

#: What a pack is allowed to contain. Everything else is refused rather than
#: ignored: a pack carrying a `.dll` is not a pack with a stray file in it, it is
#: a pack doing something it has no business doing, and silently skipping the
#: file would install it anyway under a name nobody looked at.
ALLOWED_SUFFIXES = {".json", ".png", ".webp", ".jpg", ".jpeg", ".txt", ".md"}

MANIFEST_NAME = "pack.json"

#: Pack and skin ids. Deliberately narrow — these become path segments and JSON
#: keys, and a permissive rule here is what makes the traversal check load
#: bearing rather than redundant.
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: The archive content types a pack may arrive as. Some hosts serve a zip as
#: `application/octet-stream`, which is why that is here — the manifest check
#: below is what actually decides whether the bytes are a pack.
ARCHIVE_TYPES = ("zip", "octet-stream", "x-zip")


#: Licence families that name a credit line and require it to travel with copies.
#:
#: Matched on a normalised prefix rather than an exact id, because the same
#: licence is written a dozen ways (`CC-BY-4.0`, `CC BY 4.0`, `cc-by-nc-4.0`)
#: and refusing a pack over punctuation would push authors toward writing
#: something vaguer that passes.
_ATTRIBUTION_LICENSES = ("cc-by", "ccby", "creative commons attribution")


def _needs_attribution(license_name: str) -> bool:
    normalised = license_name.strip().lower().replace("_", "-")
    collapsed = normalised.replace(" ", "-")
    return any(
        collapsed.startswith(prefix.replace(" ", "-")) or normalised.startswith(prefix)
        for prefix in _ATTRIBUTION_LICENSES
    )


class PackError(ValueError):
    """A pack could not be installed, and why."""


@dataclass(frozen=True, slots=True)
class InstalledSkin:
    """One skin out of a pack, plus where its texture lives."""

    definition: Any  # skins.SkinDefinition; imported lazily to avoid a cycle
    pack_id: str
    texture: str | None
    #: Copied down from the pack, so the credit travels with the **rendered
    #: thing** rather than only with the pack record.
    #:
    #: A player looks at a skin in the armoury; nobody opens a list of installed
    #: packs to find out who made the gun they are holding. An attribution
    #: licence requires the credit to accompany the material, and the material
    #: here is the skin.
    license: str = ""
    attribution: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.definition.to_dict())
        out["packId"] = self.pack_id
        out["license"] = self.license
        out["attribution"] = self.attribution
        # A URL rather than a filename, because the only consumer is a client
        # that has to fetch it, and building the path in three places (browser,
        # native, agent) is three chances to build it differently.
        out["textureUrl"] = (
            f"/api/hassault/skins/packs/{self.pack_id}/files/{self.texture}"
            if self.texture
            else None
        )
        return out


@dataclass(frozen=True, slots=True)
class SkinPack:
    id: str
    name: str
    version: str
    author: str
    source_url: str
    #: The licence the pack's art is under, as an SPDX id or a plain name
    #: (`CC-BY-NC-4.0`, `CC-BY-4.0`, `GPL-3.0-only`).
    #:
    #: **Required, and not defaulted to anything.** A pack is somebody else's
    #: artwork arriving on a stranger's machine, and the one field that cannot be
    #: reconstructed later is what you are allowed to do with it. Defaulting this
    #: to an empty string would let a pack install with no licence at all and
    #: look exactly like one that had a permissive one.
    license: str
    #: The credit line the licence requires, verbatim.
    #:
    #: Attribution licences do not ask for "a link somewhere" — CC BY and BY-NC
    #: name a specific string and require it to travel with every copy. Carrying
    #: it as free text rather than as a name/url pair is deliberate: the licensor
    #: writes the sentence, and a schema that reformatted it would be failing the
    #: condition while looking tidy.
    attribution: str
    #: Whether the bytes were checked against a digest the caller supplied. A
    #: pack installed without one is `False` — not "probably fine".
    verified: bool
    skins: tuple[InstalledSkin, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "license": self.license,
            "attribution": self.attribution,
            "sourceUrl": self.source_url,
            "verified": self.verified,
            "skinCount": len(self.skins),
            "skins": [s.definition.id for s in self.skins],
        }


def packs_dir() -> Path:
    root = data_dir().joinpath(*PACKS_SUBDIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _pack_path(pack_id: str) -> Path:
    if not _ID.match(pack_id):
        raise PackError(f"not a usable pack id: {pack_id!r}")
    return packs_dir() / pack_id


# -----------------------------------------------------------------------------
# Reading what is installed
# -----------------------------------------------------------------------------


def _parse_manifest(
    raw: dict[str, Any], *, pack_id: str, source: str, verified: bool
) -> SkinPack:
    """Turn a decoded `pack.json` into a `SkinPack`, or say why not.

    Built-in ids are refused **here**, at parse time, rather than filtered out
    when the catalog is assembled. A pack that got as far as being installed and
    then had half its skins quietly disappear is the worst of both: it is on
    disk, it is listed, and it does not work.
    """
    from backend.modules.hassault.skins import SKIN_DICT, Rarity, SkinDefinition

    declared = str(raw.get("id") or "")
    if declared and declared != pack_id:
        raise PackError(
            f"manifest id {declared!r} does not match the pack directory {pack_id!r}"
        )

    # Refused before anything else about the pack is considered. The SVU-A
    # rifle in `assets/horribleAssault/` is the case this exists for: its own
    # bundled readme says CC BY-NC 4.0 by Luiz Reis and names the exact credit
    # line, while the repo's provenance table had it as GPL by somebody else. A
    # format that let a licence be omitted would have carried that mistake onto
    # every machine that installed it, and there would be nothing in the
    # installed pack to notice it by.
    license_name = str(raw.get("license") or "").strip()
    if not license_name:
        raise PackError(
            "a pack has to declare a 'license' (an SPDX id like 'CC-BY-NC-4.0', "
            "or a plain name) - a pack is somebody's artwork arriving on a "
            "stranger's machine, and what they may do with it cannot be "
            "reconstructed later"
        )
    attribution = str(raw.get("attribution") or "").strip()
    # Required for the licences that require it, and only those. Demanding a
    # credit line for a public-domain pack would be asking for something nobody
    # can supply; not demanding it for a BY licence would be shipping a pack that
    # cannot be used lawfully.
    if _needs_attribution(license_name) and not attribution:
        raise PackError(
            f"'{license_name}' is an attribution licence, so the pack has to "
            "carry the credit line it requires in 'attribution'"
        )

    entries = raw.get("skins")
    if not isinstance(entries, list) or not entries:
        raise PackError("a pack has to define at least one skin")

    skins: list[InstalledSkin] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise PackError("every entry in 'skins' has to be an object")
        skin_id = str(entry.get("id") or "")
        if not _ID.match(skin_id):
            raise PackError(f"not a usable skin id: {skin_id!r}")
        if skin_id in SKIN_DICT:
            raise PackError(
                f"'{skin_id}' is already a built-in skin; a pack may not redefine one"
            )
        rarity_name = str(entry.get("rarity") or "consumer")
        try:
            rarity = Rarity(rarity_name)
        except ValueError as exc:
            allowed = ", ".join(r.value for r in Rarity)
            raise PackError(
                f"unknown rarity {rarity_name!r} (try one of: {allowed})"
            ) from exc
        texture = entry.get("texture")
        if texture is not None:
            texture = str(texture)
            # A texture name is a **file name**, never a path. The traversal
            # check below would catch a `..` anyway; refusing a separator here
            # means the error names the real problem.
            if "/" in texture or "\\" in texture or texture in {".", ".."}:
                raise PackError(f"a texture has to be a plain file name: {texture!r}")
            if Path(texture).suffix.lower() not in {".png", ".webp", ".jpg", ".jpeg"}:
                raise PackError(f"not an image: {texture!r}")
        skins.append(
            InstalledSkin(
                definition=SkinDefinition(
                    id=skin_id,
                    name=str(entry.get("name") or skin_id),
                    weapon_id=str(
                        entry.get("weaponId") or entry.get("weapon_id") or ""
                    ),
                    rarity=rarity,
                    collection=str(
                        entry.get("collection") or raw.get("name") or pack_id
                    ),
                    base_color=str(
                        entry.get("baseColor") or entry.get("base_color") or "#888888"
                    ),
                    accent_color=str(
                        entry.get("accentColor")
                        or entry.get("accent_color")
                        or "#cccccc"
                    ),
                    pattern_type=str(
                        entry.get("patternType") or entry.get("pattern_type") or "solid"
                    ),
                    description=str(entry.get("description") or ""),
                ),
                pack_id=pack_id,
                texture=texture,
                license=license_name,
                attribution=attribution,
            )
        )

    return SkinPack(
        id=pack_id,
        name=str(raw.get("name") or pack_id),
        version=str(raw.get("version") or "0"),
        author=str(raw.get("author") or ""),
        source_url=source,
        license=license_name,
        attribution=attribution,
        verified=verified,
        skins=tuple(skins),
    )


def load_pack(directory: Path) -> SkinPack:
    """Read one installed pack. Raises `PackError` if it is not usable."""
    manifest = directory / MANIFEST_NAME
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PackError(f"no {MANIFEST_NAME} in {directory.name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise PackError(
            f"{directory.name}/{MANIFEST_NAME} is not readable JSON: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise PackError(f"{directory.name}/{MANIFEST_NAME} is not an object")
    meta = raw.get("_install")
    meta = meta if isinstance(meta, dict) else {}
    return _parse_manifest(
        raw,
        pack_id=directory.name,
        source=str(meta.get("sourceUrl") or ""),
        verified=bool(meta.get("verified")),
    )


def installed_packs() -> list[SkinPack]:
    """Every usable pack on disk, sorted by id.

    A pack that fails to parse is **logged and skipped**, not raised: one bad
    directory must not cost the player every other skin they installed, and the
    log line is the thing that makes it findable. Directories ending in `.part`
    are skipped silently — those are downloads in flight, and reporting one as
    broken would be reporting a normal state as a fault.
    """
    out: list[SkinPack] = []
    root = packs_dir()
    for directory in sorted(root.iterdir()) if root.exists() else []:
        if not directory.is_dir() or directory.name.endswith(".part"):
            continue
        try:
            out.append(load_pack(directory))
        except PackError as exc:
            log.warning("hassault: skipping skin pack %s: %s", directory.name, exc)
    return out


def installed_skins() -> list[InstalledSkin]:
    """Every skin from every installed pack, flattened."""
    return [skin for pack in installed_packs() for skin in pack.skins]


def texture_path(pack_id: str, name: str) -> Path:
    """Resolve one file inside a pack, refusing anything outside it.

    The check is on the **resolved** path, not on the name: `a/../../b` contains
    no obviously bad segment after normalisation by a careless reader, and a
    symlink planted by a hostile pack points wherever it likes regardless of what
    its name looks like. `Path.resolve` answers both.
    """
    root = _pack_path(pack_id).resolve()
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root):
        raise PackError(f"{name!r} is outside the pack")
    if not candidate.is_file():
        raise PackError(f"no such file in {pack_id}: {name}")
    return candidate


# -----------------------------------------------------------------------------
# Installing
# -----------------------------------------------------------------------------


def _safe_members(archive: zipfile.ZipFile, destination: Path) -> list[zipfile.ZipInfo]:
    """The members worth extracting, or raise saying which one is not.

    Every rule here has a failure that is silent without it — see the module
    docstring. The budget check in particular is made from the **declared** sizes
    before a single byte is written, because the alternative is discovering the
    problem with a full disk and a half-written pack.
    """
    members = [m for m in archive.infolist() if not m.is_dir()]
    if not members:
        raise PackError("the archive is empty")
    if len(members) > MAX_MEMBERS:
        raise PackError(f"{len(members)} files; a pack may carry at most {MAX_MEMBERS}")

    total = sum(m.file_size for m in members)
    if total > MAX_UNPACKED_BYTES:
        raise PackError(
            f"the archive declares {total / 1e6:.1f} MB unpacked, over the "
            f"{MAX_UNPACKED_BYTES / 1e6:.0f} MB budget"
        )

    root = destination.resolve()
    for member in members:
        suffix = Path(member.filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise PackError(f"a pack may not contain {member.filename!r}")
        target = (destination / member.filename).resolve()
        if not target.is_relative_to(root):
            # Zip slip. A legal archive can name `../../anything`, and the
            # extractor will write it.
            raise PackError(f"{member.filename!r} would be written outside the pack")
    return members


def _find_manifest(archive: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    """Locate `pack.json`, at the root or one directory down.

    One level down because that is what a zip made by selecting a folder looks
    like, and refusing it would make "it works if you zip it the other way" a
    thing a user has to know.
    """
    candidates = [
        m.filename
        for m in archive.infolist()
        if Path(m.filename).name == MANIFEST_NAME and m.filename.count("/") <= 1
    ]
    if not candidates:
        raise PackError(f"no {MANIFEST_NAME} in the archive")
    # The shallowest, so a nested example pack cannot shadow the real one.
    name = min(candidates, key=lambda n: n.count("/"))
    try:
        raw = json.loads(archive.read(name).decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackError(f"{MANIFEST_NAME} is not readable JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise PackError(f"{MANIFEST_NAME} is not an object")
    return name, raw


def install_archive(
    data: bytes,
    *,
    source_url: str = "",
    sha256: str | None = None,
) -> SkinPack:
    """Validate an in-memory zip and install it. Raises `PackError`.

    Takes bytes rather than a URL so the network half and the archive half are
    separately testable — every rule above can be exercised with a zip built in
    a test, which is the only way to check a hostile archive without hosting one.
    """
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise PackError(
            f"{len(data) / 1e6:.1f} MB, over the {MAX_DOWNLOAD_BYTES / 1e6:.0f} MB limit"
        )
    verified = False
    if sha256:
        actual = hashlib.sha256(data).hexdigest()
        if actual.lower() != sha256.strip().lower():
            # Aborts before anything is written. A pack that failed its digest is
            # not a pack to install and warn about.
            raise PackError(f"digest mismatch: expected {sha256}, got {actual}")
        verified = True

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PackError(f"not a zip archive: {exc}") from exc

    with archive:
        manifest_name, raw = _find_manifest(archive)
        pack_id = str(raw.get("id") or "")
        if not _ID.match(pack_id):
            raise PackError(f"not a usable pack id: {pack_id!r}")
        # Parsed before anything is written, so a manifest naming a built-in skin
        # or an unknown rarity fails with nothing on disk to clean up.
        _parse_manifest(raw, pack_id=pack_id, source=source_url, verified=verified)

        destination = _pack_path(pack_id)
        staging = destination.with_name(destination.name + ".part")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        prefix = str(Path(manifest_name).parent).replace("\\", "/")
        prefix = "" if prefix == "." else prefix + "/"
        try:
            members = _safe_members(archive, staging)
            for member in members:
                # Flattened relative to the manifest, so a pack zipped as a
                # folder and one zipped as its contents install identically —
                # and a `texture` field that names `pulse.png` finds it either
                # way.
                relative = (
                    member.filename[len(prefix) :]
                    if member.filename.startswith(prefix)
                    else member.filename
                )
                if not relative or relative.endswith("/"):
                    continue
                target = staging / relative
                if not target.resolve().is_relative_to(staging.resolve()):
                    raise PackError(
                        f"{member.filename!r} would be written outside the pack"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 64)

            # Where it came from and whether it was checked, recorded inside the
            # pack rather than in a side table: a directory copied to another
            # machine should carry its own provenance.
            manifest_path = staging / MANIFEST_NAME
            stored = json.loads(manifest_path.read_text(encoding="utf-8"))
            stored["_install"] = {"sourceUrl": source_url, "verified": verified}
            manifest_path.write_text(json.dumps(stored, indent=2), encoding="utf-8")

            if destination.exists():
                shutil.rmtree(destination)
            staging.rename(destination)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    return load_pack(destination)


async def install_from_url(url: str, *, sha256: str | None = None) -> SkinPack:
    """Fetch a pack and install it.

    The fetch goes through the browser module's guarded one rather than a bare
    `httpx` call, which is not ceremony: this is a route that takes a URL from a
    user and makes the *server* request it, which is the definition of an SSRF
    sink. `backend/modules/browser/fetch.py` is the single place that policy
    lives, and a second copy of the redirect loop is a second guard to keep in
    step.
    """
    from backend.modules.browser.fetch import safe_fetch_bytes

    final_url, data = await safe_fetch_bytes(
        url, accept=ARCHIVE_TYPES, max_bytes=MAX_DOWNLOAD_BYTES
    )
    return install_archive(data, source_url=final_url, sha256=sha256)


def remove_pack(pack_id: str) -> bool:
    """Delete an installed pack. Returns whether there was one.

    There is no built-in to protect here — built-ins live in `skins.py` and have
    no directory — but the id is still validated, because this is a route
    argument that becomes a path and `rmtree` is not a forgiving function.
    """
    directory = _pack_path(pack_id)
    if not directory.is_dir():
        return False
    shutil.rmtree(directory)
    return True
