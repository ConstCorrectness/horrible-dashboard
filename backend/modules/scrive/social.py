"""What a post to X, LinkedIn or YouTube says, and what is checked before it may go.

Each target has a payload model (what the composer edits and the outbox stores), and
`preflight` runs that target's rules plus the shared secret scan. Preflight runs when
a person **approves** a row — and approval freezes the payload: `{{post.url}}` is
replaced by the page's published URL then, so a scheduled send ships exactly what was
approved.

Findings follow the site publisher's contract (`publish.Finding`): `blocking` stops
approval until acknowledged, and a few rules are **hard** (`HARD_RULES`) — a post that
is too long, a file that is not there, a target that is not connected — which no
acknowledgement can get past, because the platform would refuse it anyway.

The X character counter mirrors twitter-text's weighting (and `social/xcount.ts`,
which the composer counts with as you type): most Latin, Cyrillic and Greek text and
general punctuation weigh 1, everything else (CJK, emoji) 2; every URL counts 23; the
limit is 280.
"""

from __future__ import annotations

import json
import mimetypes
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.modules.connectors import store as connector_store
from backend.modules.connectors.providers import linkedin
from backend.modules.scrive import clips, publish, store
from backend.modules.scrive.publish import Finding
from backend.modules.settings.routes import get_value
from backend.publishing.scan import scan_text

Target = Literal["x", "linkedin", "youtube"]
TARGETS: tuple[str, ...] = ("x", "linkedin", "youtube")
LABELS = {"x": "X", "linkedin": "LinkedIn", "youtube": "YouTube"}

#: Replaced at approval by the page's published URL.
POST_URL = "{{post.url}}"

#: Rules no acknowledgement gets past: the platform would refuse the post anyway, or
#: the row cannot be sent at all.
HARD_RULES = {
    "not-connected",
    "unpublished",
    "too-long",
    "empty",
    "missing-file",
    "media",
    "not-video",
    "bad-text",
    "publish-at",
}

# X (pay-per-use since February 2026; the prices the composer estimates with).
X_LIMIT = 280
X_URL_LENGTH = 23
X_PRICE_POST = 0.015
X_PRICE_URL_POST = 0.20
# Media limits come from the clip editor's table, so a render made for X passes here.
X_MAX_IMAGE = clips.X_IMAGE_BYTES
X_MAX_GIF = clips.X_GIF_BYTES
X_MAX_VIDEO = clips.X_VIDEO_BYTES
X_MAX_VIDEO_SECONDS = clips.X_VIDEO_SECONDS

LINKEDIN_LIMIT = 3000
YOUTUBE_TITLE = 100
YOUTUBE_DESCRIPTION = 5000
YOUTUBE_DAILY_UPLOADS = 6  # 10,000 units a day at 1,600 an upload
AUDITED_SETTING = "scrive.youtube.audited"
LINK_IN_REPLY_SETTING = "scrive.x.linkInReply"


# --- payloads -------------------------------------------------------------------------


class XPost(BaseModel):
    text: str = ""
    #: Site paths of images (up to four) or one video.
    media: list[str] = Field(default_factory=list)


class XPayload(BaseModel):
    """A post, or a thread: each post replies to the one before."""

    posts: list[XPost] = Field(default_factory=lambda: [XPost()])
    #: The page's link. Posted as a final reply when `link_in_reply` (the
    #: `scrive.x.linkInReply` setting when unset), else appended to the first post.
    link: str = POST_URL
    link_in_reply: bool | None = None


class LinkedInPayload(BaseModel):
    """A post whose content is a link card (LinkedIn's API cannot post an Article)."""

    text: str = ""
    link: str = POST_URL
    title: str = ""
    description: str = ""
    #: A site path; uploaded as the card's image.
    thumbnail: str = ""


class YouTubePayload(BaseModel):
    video: str = ""
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    privacy: Literal["public", "unlisted", "private"] = "private"
    #: ISO 8601 (UTC). Scheduling on YouTube's side: the video uploads private and
    #: goes public then.
    publish_at: str = ""
    category: str = "28"  # Science & Technology
    made_for_kids: bool = False
    #: "Altered or synthetic content" — YouTube's disclosure for realistic AI media.
    synthetic: bool = False


PAYLOADS: dict[str, type[BaseModel]] = {
    "x": XPayload,
    "linkedin": LinkedInPayload,
    "youtube": YouTubePayload,
}


def parse_payload(target: str, payload: dict[str, Any]) -> BaseModel:
    model = PAYLOADS.get(target)
    if model is None:
        raise store.StoreError(f"not a target: {target!r}")
    try:
        return model.model_validate(payload)
    except ValueError as exc:
        raise store.StoreError(f"payload for {LABELS[target]}: {exc}") from exc


# --- X counting -----------------------------------------------------------------------

_URL = re.compile(r"https?://[^\s<>\"']+|\{\{\s*post\.url\s*\}\}", re.I)
#: twitter-text v3: code points in these ranges weigh 100, all others 200 (of 100).
_LIGHT = ((0x0000, 0x10FF), (0x2000, 0x200D), (0x2010, 0x201F), (0x2032, 0x2037))
#: Joiners and modifiers that ride on the emoji before them and add nothing.
_RIDERS = re.compile("[‍︎️\U0001f3fb-\U0001f3ff]")


def _weight(ch: str) -> int:
    code = ord(ch)
    return 100 if any(lo <= code <= hi for lo, hi in _LIGHT) else 200


def x_length(text: str) -> int:
    """Weighted length as X counts it (280 is the limit)."""
    text = unicodedata.normalize("NFC", text)
    total = 0
    last = 0
    for match in _URL.finditer(text):
        total += _plain_weight(text[last : match.start()])
        total += X_URL_LENGTH * 100
        last = match.end()
    total += _plain_weight(text[last:])
    return (total + 99) // 100


def _plain_weight(text: str) -> int:
    weight = 0
    previous_heavy = False
    for ch in text:
        if previous_heavy and _RIDERS.match(ch):
            continue
        w = _weight(ch)
        weight += w
        previous_heavy = w == 200
    return weight


def x_thread(payload: XPayload) -> list[XPost]:
    """The posts as they will be sent, with the link placed."""
    posts = [p.model_copy() for p in payload.posts if p.text.strip() or p.media]
    link = payload.link.strip()
    in_reply = (
        payload.link_in_reply
        if payload.link_in_reply is not None
        else bool(get_value(LINK_IN_REPLY_SETTING, True))
    )
    if link:
        if in_reply or not posts:
            posts.append(XPost(text=link))
        else:
            posts[0] = XPost(
                text=f"{posts[0].text.rstrip()}\n\n{link}", media=posts[0].media
            )
    return posts


def x_cost(payload: XPayload) -> tuple[float, int, int]:
    """`(estimated USD, posts, posts with a URL)`."""
    posts = x_thread(payload)
    with_url = sum(1 for p in posts if _URL.search(p.text))
    return (
        round(with_url * X_PRICE_URL_POST + (len(posts) - with_url) * X_PRICE_POST, 3),
        len(posts),
        with_url,
    )


# --- the page's URL -------------------------------------------------------------------


def page_dir(rel: str) -> str:
    """Mirror of `site/urls.ts` `pageDir`: `posts/x.md` → `posts/x/`."""
    stem = re.sub(r"\.(md|ipynb)$", "", rel, flags=re.I)
    if stem == "index":
        return ""
    if stem.endswith("/index"):
        return stem[: -len("index")]
    return f"{stem}/"


def published_url(site_id: str, page: str) -> str | None:
    """The page's live URL — only once the site has been published in static mode with
    this page in it. (Jupyter Book picks its own URLs, which Scrive does not predict.)"""
    record = publish.read_record(site_id)
    if record is None or record.mode != "static" or not page:
        return None
    if f"{page_dir(page)}index.html" not in record.files:
        return None
    return record.url + page_dir(page)


def _uses_url(value: Any) -> bool:
    return POST_URL in json.dumps(value)


def resolve(target: str, payload: dict[str, Any], url: str) -> dict[str, Any]:
    """The payload with `{{post.url}}` filled in — what approval freezes."""
    return json.loads(json.dumps(payload).replace(POST_URL, url))


# --- preflight ------------------------------------------------------------------------


def _hard(rule: str, message: str, file: str = "") -> Finding:
    return Finding(
        blocking=True, severity="warning", rule=rule, message=message, file=file
    )


def _file(site_id: str, rel: str) -> tuple[int, str] | None:
    """`(size, mime)` of a site file, or None when it is not there."""
    try:
        path = store.resolve_asset(site_id, rel)
    except (FileNotFoundError, store.StoreError):
        return None
    return path.stat().st_size, mimetypes.guess_type(path.name)[0] or ""


def _texts(target: str, model: BaseModel) -> list[str]:
    if isinstance(model, XPayload):
        return [p.text for p in model.posts] + [model.link]
    if isinstance(model, LinkedInPayload):
        return [model.text, model.title, model.description]
    if isinstance(model, YouTubePayload):
        return [model.title, model.description, *model.tags]
    return []


def preflight(
    site_id: str,
    page: str,
    target: str,
    payload: dict[str, Any],
    *,
    sent_today: int = 0,
) -> list[Finding]:
    model = parse_payload(target, payload)
    findings: list[Finding] = []
    label = LABELS[target]

    if not connector_store.is_connected(target):
        findings.append(
            _hard(
                "not-connected",
                f"{label} isn't connected — connect it from the home page.",
            )
        )
    if _uses_url(payload) and published_url(site_id, page) is None:
        findings.append(
            _hard(
                "unpublished",
                "The post links to its page, which is not on the published site yet. "
                "Publish the site (static mode) first, or replace {{post.url}} with a link.",
            )
        )
    for text in _texts(target, model):
        for hit in scan_text(text):
            findings.append(
                Finding(
                    blocking=hit.blocking,
                    severity="secret" if hit.kind == "secret" else "warning",
                    rule=hit.kind,
                    message=f"{hit.label} ({hit.excerpt})",
                )
            )

    if isinstance(model, XPayload):
        findings += _x_rules(site_id, model)
    elif isinstance(model, LinkedInPayload):
        findings += _linkedin_rules(site_id, model)
    elif isinstance(model, YouTubePayload):
        findings += _youtube_rules(site_id, model, sent_today)
    return findings


def _x_rules(site_id: str, model: XPayload) -> list[Finding]:
    findings: list[Finding] = []
    posts = x_thread(model)
    if not posts:
        findings.append(_hard("empty", "There is nothing to post."))
    for i, post in enumerate(posts, 1):
        where = f"post {i}" if len(posts) > 1 else "the post"
        length = x_length(post.text)
        if length > X_LIMIT:
            findings.append(
                _hard(
                    "too-long",
                    f"{where.capitalize()} is {length}/{X_LIMIT} characters.",
                )
            )
        videos = images = 0
        for rel in post.media:
            info = _file(site_id, rel)
            if info is None:
                findings.append(
                    _hard("missing-file", f"{rel} is not in the site.", rel)
                )
                continue
            size, mime = info
            if mime.startswith("video/"):
                videos += 1
                if size > X_MAX_VIDEO:
                    findings.append(
                        _hard("media", f"{rel} is over X's 512 MB video limit.", rel)
                    )
                seconds = _duration(str(store.resolve_asset(site_id, rel)))
                if seconds and seconds > X_MAX_VIDEO_SECONDS + 0.5:
                    findings.append(
                        _hard(
                            "media",
                            f"{rel} is {int(seconds)} seconds; X takes videos up to "
                            f"{int(X_MAX_VIDEO_SECONDS)}. Trim it in the clip editor.",
                            rel,
                        )
                    )
            elif mime == "image/gif":
                # An animated GIF counts as X's one video-like attachment.
                videos += 1
                if size > X_MAX_GIF:
                    findings.append(
                        _hard("media", f"{rel} is over X's 15 MB GIF limit.", rel)
                    )
            elif mime.startswith("image/"):
                images += 1
                if size > X_MAX_IMAGE:
                    findings.append(
                        _hard("media", f"{rel} is over X's 5 MB image limit.", rel)
                    )
            else:
                findings.append(
                    _hard("media", f"{rel} is not an image or a video.", rel)
                )
        if videos > 1 or images > 4 or (videos and images):
            findings.append(
                _hard(
                    "media",
                    f"{where.capitalize()} can carry four images, or one video or GIF.",
                )
            )
    cost, count, with_url = x_cost(model)
    findings.append(
        Finding(
            severity="info",
            rule="cost",
            message=f"X charges per post: about ${cost:.2f} for {count} post"
            f"{'s' if count != 1 else ''}"
            + (f", {with_url} with a link" if with_url else "")
            + ".",
        )
    )
    return findings


def _linkedin_rules(site_id: str, model: LinkedInPayload) -> list[Finding]:
    findings: list[Finding] = []
    if not model.text.strip() and not model.link.strip():
        findings.append(_hard("empty", "There is nothing to share."))
    if len(model.text) > LINKEDIN_LIMIT:
        findings.append(
            _hard(
                "too-long",
                f"The text is {len(model.text)}/{LINKEDIN_LIMIT} characters.",
            )
        )
    if model.thumbnail:
        info = _file(site_id, model.thumbnail)
        if info is None:
            findings.append(
                _hard(
                    "missing-file",
                    f"{model.thumbnail} is not in the site.",
                    model.thumbnail,
                )
            )
        elif not info[1].startswith("image/"):
            findings.append(
                _hard("media", f"{model.thumbnail} is not an image.", model.thumbnail)
            )
    cred, _ = connector_store.load_or_error("linkedin")
    note = linkedin.expiry_note(
        linkedin.days_left(cred), bool(cred and cred.refresh_token)
    )
    if note:
        findings.append(Finding(rule="expiry", message=note))
    return findings


_CHAPTER = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s+\S", re.M)


def chapters(description: str) -> list[int]:
    """Chapter start times (seconds) written in a description, in order."""
    return [
        int(h or 0) * 3600 + int(m) * 60 + int(s)
        for h, m, s in _CHAPTER.findall(description)
    ]


def _duration(path: str) -> float | None:
    """Seconds, by ffprobe when it is installed; None otherwise."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        return float(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _youtube_rules(
    site_id: str, model: YouTubePayload, sent_today: int
) -> list[Finding]:
    findings: list[Finding] = []
    info = _file(site_id, model.video) if model.video else None
    if not model.video or info is None:
        findings.append(
            _hard(
                "missing-file",
                f"{model.video or 'The video'} is not in the site.",
                model.video,
            )
        )
    elif not info[1].startswith("video/"):
        findings.append(
            _hard("not-video", f"{model.video} is not a video file.", model.video)
        )
    else:
        seconds = _duration(str(store.resolve_asset(site_id, model.video)))
        if seconds and seconds > 15 * 60:
            findings.append(
                Finding(
                    rule="duration",
                    message=f"The video is {int(seconds // 60)} minutes; over 15 minutes "
                    "needs a verified YouTube account.",
                )
            )
    if not model.title.strip():
        findings.append(_hard("empty", "A YouTube video needs a title."))
    if len(model.title) > YOUTUBE_TITLE:
        findings.append(
            _hard(
                "too-long",
                f"The title is {len(model.title)}/{YOUTUBE_TITLE} characters.",
            )
        )
    if len(model.description) > YOUTUBE_DESCRIPTION:
        findings.append(
            _hard(
                "too-long",
                f"The description is {len(model.description)}/{YOUTUBE_DESCRIPTION} characters.",
            )
        )
    if any(c in f"{model.title}{model.description}" for c in "<>"):
        findings.append(
            _hard("bad-text", "YouTube refuses < and > in a title or description.")
        )
    if model.publish_at:
        try:
            from datetime import datetime

            when = datetime.fromisoformat(model.publish_at.replace("Z", "+00:00"))
            if when.timestamp() <= time.time():
                findings.append(
                    _hard("publish-at", "The YouTube publish time has passed.")
                )
        except ValueError:
            findings.append(
                _hard("publish-at", f"Not a date and time: {model.publish_at!r}.")
            )
    if not bool(get_value(AUDITED_SETTING, False)):
        findings.append(
            Finding(
                rule="private-lock",
                message="This Google Cloud project has not passed YouTube's API audit, so "
                "YouTube will keep the video private whatever is chosen here. Make it "
                "public in YouTube Studio afterwards.",
            )
        )
    marks = chapters(model.description)
    if marks and (
        marks[0] != 0
        or len(marks) < 3
        or any(b - a < 10 for a, b in zip(marks, marks[1:], strict=False))
    ):
        findings.append(
            Finding(
                rule="chapters",
                message="YouTube shows chapters only when the first is 0:00, there are at "
                "least three, and each is at least 10 seconds long.",
            )
        )
    if sent_today >= YOUTUBE_DAILY_UPLOADS:
        findings.append(
            Finding(
                rule="quota",
                message=f"{sent_today} uploads today already; the default API quota allows "
                f"about {YOUTUBE_DAILY_UPLOADS} a day.",
            )
        )
    return findings


def youtube_metadata(model: YouTubePayload) -> dict[str, Any]:
    """The `videos.insert` body for a payload."""
    status: dict[str, Any] = {
        "privacyStatus": "private" if model.publish_at else model.privacy,
        "selfDeclaredMadeForKids": model.made_for_kids,
        "containsSyntheticMedia": model.synthetic,
    }
    if model.publish_at:
        status["publishAt"] = model.publish_at
    return {
        "snippet": {
            "title": model.title,
            "description": model.description,
            "tags": model.tags,
            "categoryId": model.category,
        },
        "status": status,
    }


def blocked(findings: list[Finding], acknowledged: bool) -> bool:
    hard = any(f.blocking and f.rule in HARD_RULES for f in findings)
    return hard or (any(f.blocking for f in findings) and not acknowledged)


# --- a starting point for each composer ----------------------------------------------


def blank(target: str) -> dict[str, Any]:
    """An empty payload for a post that belongs to no page: no `{{post.url}}`, which
    only a page's published URL could fill."""
    model = parse_payload(target, {})
    if isinstance(model, (XPayload, LinkedInPayload)):
        model.link = ""
    return model.model_dump()


def suggest(site_id: str, page: str, target: str) -> dict[str, Any]:
    """A first draft of a payload from the page's frontmatter: title, description,
    tags, thumbnail, and its headings as YouTube chapters (times to fill in)."""
    from backend.modules.scrive import sections

    text = store.read_page(site_id, page).content
    fm = sections.frontmatter_of(text)
    title = str(fm.get("title") or PurePosixPath(page).stem)
    description = str(fm.get("description") or "")
    tags = (
        [str(t) for t in fm.get("tags") or []]
        if isinstance(fm.get("tags"), list)
        else []
    )
    thumb = str(fm.get("thumbnail") or "")
    if thumb and not thumb.startswith(("http://", "https://")):
        base = PurePosixPath(page).parent
        thumb = (
            str(PurePosixPath(base, thumb))
            if not thumb.startswith("/")
            else thumb.lstrip("/")
        )
        thumb = re.sub(r"(^|/)[^/]+/\.\./", r"\1", thumb)
    if target == "x":
        lead = f"{title}\n\n{description}".strip()
        return XPayload(posts=[XPost(text=lead)]).model_dump()
    if target == "linkedin":
        return LinkedInPayload(
            text=description or title,
            title=title,
            description=description,
            thumbnail=thumb,
        ).model_dump()
    heads = [h.text for h in sections.headings(text) if h.level == 2]
    chapter_lines = "\n".join(f"0:00 {h}" for h in heads)
    body = description
    if chapter_lines:
        body += f"\n\nChapters (set the times):\n{chapter_lines}"
    body += f"\n\nThe post: {POST_URL}"
    return YouTubePayload(
        title=title[:YOUTUBE_TITLE], description=body.strip(), tags=tags
    ).model_dump()
