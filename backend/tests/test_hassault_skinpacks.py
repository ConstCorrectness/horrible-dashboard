"""Skin packs: the archive rules, and what happens when one is hostile.

Every case here is a failure that is silent without the check it exercises — a
pack that writes outside its directory, one that shadows a built-in, one that
declares forty gigabytes, one that leaves half a directory behind when it fails.
None of those raise on their own; the whole point of the module is that they are
turned into refusals with a reason.

The archives are built in-process rather than fetched. There is no other way to
test a hostile zip: you cannot host one, and a fixture file checked into the repo
would be an actual zip-slip archive sitting in the tree.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from backend.modules.hassault import skinpacks
from backend.modules.hassault.skinpacks import PackError


@pytest.fixture(autouse=True)
def packs_root(tmp_path, monkeypatch):
    """Point the packs directory at a temp dir, and forget the catalog cache.

    Both halves matter: without the first these tests write into the developer's
    real data directory, and without the second a pack installed by one test is
    still in `skins.catalog()` during the next one for up to the cache's TTL.
    """
    from backend.modules.hassault import skins

    monkeypatch.setattr(skinpacks, "data_dir", lambda: tmp_path)
    skins.invalidate_pack_cache()
    yield tmp_path
    skins.invalidate_pack_cache()


def manifest(
    pack_id: str = "neon",
    skin_id: str = "neon_pulse",
    skin: dict | None = None,
    pack: dict | None = None,
) -> dict:
    entry = {
        "id": skin_id,
        "name": "Pulse",
        "weaponId": "assault",
        "rarity": "covert",
        "collection": "Neon",
        "baseColor": "#22d3ee",
        "accentColor": "#f472b6",
        "patternType": "custom_art",
        "description": "a test skin",
    }
    entry.update(skin or {})
    out = {
        "id": pack_id,
        "name": "Neon",
        "version": "1.0.0",
        # Required. A pack with no licence does not install — see the licence
        # tests at the bottom of this file.
        "license": "CC0-1.0",
        "skins": [entry],
    }
    out.update(pack or {})
    return out


def archive(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def pack_zip(
    manifest_dict: dict | None = None, *, prefix: str = "", textures=()
) -> bytes:
    members = {
        f"{prefix}pack.json": json.dumps(manifest_dict or manifest()).encode(),
    }
    for name in textures:
        members[f"{prefix}{name}"] = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    return archive(members)


# -- the happy path -----------------------------------------------------------


def test_a_pack_installs_and_its_skins_join_the_catalog():
    from backend.modules.hassault import skins

    pack = skinpacks.install_archive(
        pack_zip(), source_url="https://example.test/neon.zip"
    )
    assert pack.id == "neon"
    assert [s.definition.id for s in pack.skins] == ["neon_pulse"]

    skins.invalidate_pack_cache()
    ids = [s.id for s in skins.catalog()]
    assert "neon_pulse" in ids
    # The built-ins are still first and still all there: a pack adds, it never
    # replaces.
    assert ids[: len(skins.SKIN_CATALOG)] == [s.id for s in skins.SKIN_CATALOG]


def test_a_pack_zipped_as_a_folder_installs_the_same_way():
    # A zip made by right-clicking a folder has everything one level down. If
    # that failed, "it works if you zip it the other way" becomes something a
    # user has to know — and the `texture` field, which names a bare file name,
    # would resolve in one layout and not the other.
    data = pack_zip(
        manifest(skin={"texture": "pulse.png"}), prefix="neon/", textures=["pulse.png"]
    )
    pack = skinpacks.install_archive(data)
    assert pack.skins[0].texture == "pulse.png"
    # Flattened, so the served path has no folder in it.
    assert skinpacks.texture_path("neon", "pulse.png").is_file()


def test_a_texture_url_is_built_once_by_the_backend():
    # Three clients would otherwise each build this path, which is three chances
    # to build it differently.
    skinpacks.install_archive(
        pack_zip(manifest(skin={"texture": "pulse.png"}), textures=["pulse.png"])
    )
    served = skinpacks.installed_skins()[0].to_dict()
    assert served["textureUrl"] == "/api/hassault/skins/packs/neon/files/pulse.png"
    assert served["packId"] == "neon"


def test_a_pack_skin_without_a_texture_is_still_a_skin():
    # The whole reason a pack is useful before either client can draw a texture.
    skinpacks.install_archive(pack_zip())
    assert skinpacks.installed_skins()[0].to_dict()["textureUrl"] is None


# -- the archive rules --------------------------------------------------------


def test_a_member_that_escapes_the_pack_is_refused():
    # Zip slip. `../../evil.json` is a legal name inside a zip and `extractall`
    # writes it without complaint.
    data = archive(
        {"pack.json": json.dumps(manifest()).encode(), "../../evil.json": b"{}"}
    )
    with pytest.raises(PackError, match="outside the pack"):
        skinpacks.install_archive(data)


def test_a_pack_may_not_redefine_a_builtin_skin():
    from backend.modules.hassault.skins import SKIN_CATALOG

    victim = SKIN_CATALOG[0].id
    with pytest.raises(PackError, match="built-in"):
        skinpacks.install_archive(pack_zip(manifest(skin_id=victim)))


def test_a_declared_size_over_the_budget_is_refused_before_extracting():
    # Checked from the *declared* sizes, so this never has to create the bytes —
    # which is exactly the property that makes it a pre-flight check and not a
    # discovery that the disk is full.
    data = pack_zip()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = zf.infolist()
    monkey = zipfile.ZipFile(io.BytesIO(data))
    for info in monkey.infolist():
        info.file_size = skinpacks.MAX_UNPACKED_BYTES + 1
    with pytest.raises(PackError, match="budget"):
        skinpacks._safe_members(monkey, skinpacks.packs_dir() / "x")
    assert infos  # the fixture really did contain something


def test_a_file_type_a_pack_has_no_business_carrying_is_refused():
    data = archive({"pack.json": json.dumps(manifest()).encode(), "payload.dll": b"MZ"})
    with pytest.raises(PackError, match="may not contain"):
        skinpacks.install_archive(data)


def test_a_texture_naming_a_path_is_refused():
    with pytest.raises(PackError, match="plain file name"):
        skinpacks.install_archive(
            pack_zip(manifest(skin={"texture": "../../etc/passwd.png"}))
        )


def test_a_failed_install_leaves_nothing_behind():
    # The `.part` staging directory exists so a half-written pack is never
    # scanned as installed. A failure part-way has to take it with it.
    data = archive(
        {"pack.json": json.dumps(manifest()).encode(), "nested/../../out.png": b"x"}
    )
    with pytest.raises(PackError):
        skinpacks.install_archive(data)
    assert list(skinpacks.packs_dir().iterdir()) == []


def test_a_bad_digest_aborts_before_writing():
    with pytest.raises(PackError, match="digest mismatch"):
        skinpacks.install_archive(pack_zip(), sha256="00" * 32)
    assert list(skinpacks.packs_dir().iterdir()) == []


def test_a_matching_digest_marks_the_pack_verified():
    data = pack_zip()
    pack = skinpacks.install_archive(data, sha256=hashlib.sha256(data).hexdigest())
    assert pack.verified is True
    # And it survives a re-read: provenance is stored inside the pack so a
    # directory copied to another machine carries it.
    assert skinpacks.load_pack(skinpacks.packs_dir() / "neon").verified is True


def test_a_pack_installed_without_a_digest_is_not_claimed_to_be_verified():
    pack = skinpacks.install_archive(pack_zip())
    assert pack.verified is False


def test_an_archive_with_no_manifest_is_refused():
    with pytest.raises(PackError, match="pack.json"):
        skinpacks.install_archive(archive({"readme.md": b"hello"}))


def test_an_unknown_rarity_names_the_ones_that_work():
    with pytest.raises(PackError, match="unknown rarity"):
        skinpacks.install_archive(pack_zip(manifest(skin={"rarity": "legendary"})))


# -- reading what is installed ------------------------------------------------


def test_one_broken_pack_does_not_cost_the_others():
    skinpacks.install_archive(pack_zip())
    broken = skinpacks.packs_dir() / "broken"
    broken.mkdir()
    (broken / "pack.json").write_text("{not json", encoding="utf-8")
    assert [p.id for p in skinpacks.installed_packs()] == ["neon"]


def test_a_download_in_flight_is_not_reported_as_broken():
    skinpacks.install_archive(pack_zip())
    (skinpacks.packs_dir() / "half.part").mkdir()
    assert [p.id for p in skinpacks.installed_packs()] == ["neon"]


def test_removing_a_pack_takes_its_skins_out_of_the_catalog():
    from backend.modules.hassault import skins

    skinpacks.install_archive(pack_zip())
    skins.invalidate_pack_cache()
    assert "neon_pulse" in [s.id for s in skins.catalog()]

    assert skinpacks.remove_pack("neon") is True
    skins.invalidate_pack_cache()
    assert "neon_pulse" not in [s.id for s in skins.catalog()]
    assert skinpacks.remove_pack("neon") is False


def test_a_pack_id_that_is_not_an_id_never_reaches_the_filesystem():
    # This argument arrives from a route and becomes a path that `rmtree` is
    # pointed at.
    for hostile in ["../../etc", "a/b", "", "A" * 200]:
        with pytest.raises(PackError):
            skinpacks.remove_pack(hostile)


def test_a_file_outside_the_pack_is_not_served():
    skinpacks.install_archive(pack_zip())
    outside = skinpacks.packs_dir() / "secret.txt"
    outside.write_text("no", encoding="utf-8")
    with pytest.raises(PackError, match="outside the pack"):
        skinpacks.texture_path("neon", "../secret.txt")


# -- licence and attribution --------------------------------------------------


def test_a_pack_with_no_licence_does_not_install():
    # A pack is somebody else's artwork arriving on a stranger's machine, and
    # what they may do with it is the one field that cannot be reconstructed
    # afterwards. Defaulting it to empty would make a pack with no licence look
    # exactly like one carrying a permissive one.
    bare = manifest()
    del bare["license"]
    with pytest.raises(PackError, match="license"):
        skinpacks.install_archive(pack_zip(bare))


def test_an_attribution_licence_without_a_credit_line_does_not_install():
    # The case this exists for is real and in this repo. The SVU-A rifle in
    # assets/horribleAssault/ carries its own readme saying CC BY-NC 4.0 by Luiz
    # Reis, naming the exact credit line to use, while the provenance table had
    # it as GPL by somebody else. A pack format that let the licence be omitted
    # would have carried that mistake onto every machine that installed it, with
    # nothing in the installed pack to notice it by.
    with pytest.raises(PackError, match="attribution licence"):
        skinpacks.install_archive(pack_zip(manifest(pack={"license": "CC-BY-NC-4.0"})))


@pytest.mark.parametrize(
    "spelling",
    ["CC-BY-4.0", "CC BY 4.0", "cc-by-nc-4.0", "Creative Commons Attribution 4.0"],
)
def test_attribution_is_required_however_the_licence_is_spelled(spelling):
    # Matched on a normalised prefix rather than an exact id: the same licence is
    # written a dozen ways, and refusing a pack over punctuation pushes authors
    # toward writing something vaguer that passes.
    with pytest.raises(PackError, match="attribution licence"):
        skinpacks.install_archive(pack_zip(manifest(pack={"license": spelling})))


def test_a_credit_line_survives_the_install_verbatim():
    # Verbatim, because the licensor writes the sentence. A schema that
    # reformatted it into a name and a URL would be failing the condition while
    # looking tidier.
    credit = "SVU-A sniper rifle by Luiz Reis (https://www.artstation.com/izreis)"
    pack = skinpacks.install_archive(
        pack_zip(manifest(pack={"license": "CC-BY-NC-4.0", "attribution": credit}))
    )
    assert pack.attribution == credit
    assert pack.license == "CC-BY-NC-4.0"
    # And on re-read, so a pack directory copied to another machine carries it.
    reread = skinpacks.load_pack(skinpacks.packs_dir() / "neon")
    assert reread.attribution == credit
    assert reread.to_dict()["attribution"] == credit
    # And onto the skin itself, which is the thing a player actually looks at.
    # Nobody opens a list of installed packs to find out who made the gun they
    # are holding.
    assert reread.skins[0].to_dict()["attribution"] == credit


def test_a_licence_that_needs_no_credit_line_does_not_demand_one():
    # Demanding attribution for a public-domain pack asks for something nobody
    # can supply.
    pack = skinpacks.install_archive(pack_zip(manifest(pack={"license": "CC0-1.0"})))
    assert pack.attribution == ""
