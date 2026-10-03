"""How an approved outbox row is actually sent, one target at a time, as steps.

Every step is checkpointed in the row before and after it runs (`Job.save`), which is
what makes a send resumable:

- an **upload** step keeps its session and offset (X's media id and next segment,
  YouTube's session URL and confirmed byte offset), so a restart continues it;
- a **post** step (`kind: post`) is the one that cannot be repeated safely. It is saved
  `running` before the request; on success `done` with the new id. If the request got
  no answer at all (network failure), or the process died while it was out, nobody
  knows whether it posted: the step becomes `unknown` and the row fails with a message
  saying so. Posting again is the person's call (Retry), never the runner's.

An HTTP error from the platform *is* an answer — the post was not created — so the step
goes back to `pending` and the error is retried or reported by the runner.
"""

from __future__ import annotations

import asyncio
import mimetypes
from collections.abc import Awaitable, Callable
from typing import Any

from backend.modules.connectors.providers import devto, hashnode, linkedin, x, youtube
from backend.modules.connectors.providers.social_http import SendError
from backend.modules.scrive import outbox, social, store

#: Bytes per X APPEND (X accepts up to 5 MB a segment).
X_CHUNK = 4 * 1024 * 1024
#: Bytes per YouTube chunk; must be a multiple of 256 KiB.
YOUTUBE_CHUNK = 8 * 1024 * 1024
#: How long to wait for X to finish processing a video, at most.
X_PROCESSING_LIMIT_S = 600.0


class Job:
    """One row being sent: its payload, its steps, and how to report progress."""

    def __init__(
        self,
        item: outbox.OutboxItem,
        progress: Callable[[str, float | None], Awaitable[None]] | None = None,
    ) -> None:
        self.item = item
        self._progress = progress

    @property
    def site(self) -> str:
        return self.item.site

    def step(self, name: str) -> dict[str, Any]:
        return dict(self.item.steps.get(name) or {})

    def save(self, name: str, status: str, **data: Any) -> None:
        self.item = outbox.save_step(self.item.id, name, status, **data)

    async def progress(self, label: str, fraction: float | None = None) -> None:
        if self._progress:
            await self._progress(label, fraction)


async def _read(path: Any, offset: int, size: int) -> bytes:
    def read() -> bytes:
        with open(path, "rb") as handle:
            handle.seek(offset)
            return handle.read(size)

    return await asyncio.to_thread(read)


async def _post_step(
    job: Job,
    name: str,
    label: str,
    request: Callable[[], Awaitable[str | tuple[str, str]]],
) -> str:
    """Run a non-repeatable post once, recording its outcome (see the module docstring).
    A request may answer `(id, url)` when the platform names the URL; the step keeps
    it, so a resumed send still knows where the post is."""
    step = job.step(name)
    if step.get("status") == "done":
        return str(step.get("id") or "")
    if step.get("status") in ("running", "unknown"):
        job.save(name, "unknown", kind="post")
        raise SendError(
            f"It is not known whether {label} went out — the send was interrupted while "
            "it was in flight. Check the account before choosing Retry, which posts it "
            "again."
        )
    job.save(name, "running", kind="post")
    try:
        answer = await request()
    except SendError as exc:
        if exc.status is None:
            job.save(name, "unknown", kind="post")
            raise SendError(
                f"{label} may or may not have gone out: {exc}. Check the account before "
                "choosing Retry, which posts it again."
            ) from exc
        job.save(name, "pending", kind="post")
        raise
    if isinstance(answer, tuple):
        new_id, url = answer
        job.save(name, "done", kind="post", id=new_id, url=url)
    else:
        new_id = answer
        job.save(name, "done", kind="post", id=new_id)
    return new_id


# --- X ------------------------------------------------------------------------------------


async def _x_media(job: Job, name: str, rel: str) -> str:
    step = job.step(name)
    if step.get("status") == "done":
        return str(step["media_id"])
    path = store.resolve_asset(job.site, rel)
    size = path.stat().st_size
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    category = (
        "tweet_video"
        if mime.startswith("video/")
        else "tweet_gif"
        if mime == "image/gif"
        else "tweet_image"
    )
    media_id = str(step.get("media_id") or "")
    segment = int(step.get("segment") or 0)
    if not media_id:
        media_id = await x.media_initialize(size, mime, category)
        segment = 0
        job.save(name, "uploading", media_id=media_id, segment=0)
    while segment * X_CHUNK < size:
        chunk = await _read(path, segment * X_CHUNK, X_CHUNK)
        await x.media_append(media_id, segment, chunk)
        segment += 1
        job.save(name, "uploading", media_id=media_id, segment=segment)
        await job.progress(f"uploading {rel}", min(1.0, segment * X_CHUNK / size))
    info = await x.media_finalize(media_id)
    waited = 0.0
    while (
        info.get("state") in ("pending", "in_progress")
        and waited < X_PROCESSING_LIMIT_S
    ):
        delay = float(info.get("check_after_secs") or 5)
        await job.progress(f"X is processing {rel}", None)
        await asyncio.sleep(delay)
        waited += delay
        info = await x.media_status(media_id)
    if info.get("state") == "failed":
        error = (info.get("error") or {}).get("message") or "processing failed"
        raise SendError(f"X could not use {rel}: {error}")
    if info.get("state") in ("pending", "in_progress"):
        raise SendError(f"X is still processing {rel}", transient=True)
    job.save(name, "done", media_id=media_id)
    return media_id


async def send_x(job: Job) -> tuple[str, str]:
    payload = social.XPayload.model_validate(job.item.payload)
    posts = social.x_thread(payload)
    first = ""
    reply_to: str | None = None
    for i, post in enumerate(posts):
        media = [
            await _x_media(job, f"media-{i}-{j}", rel)
            for j, rel in enumerate(post.media)
        ]
        label = f"post {i + 1} of {len(posts)}" if len(posts) > 1 else "the post"
        await job.progress(f"posting {label}", i / len(posts))
        post_id = await _post_step(
            job,
            f"post-{i}",
            label,
            lambda text=post.text, reply=reply_to, ids=media: x.create_post(
                text, reply_to=reply, media_ids=ids or None
            ),
        )
        first = first or post_id
        reply_to = post_id
    return first, x.post_url(first)


# --- LinkedIn -----------------------------------------------------------------------------


async def send_linkedin(job: Job) -> tuple[str, str]:
    payload = social.LinkedInPayload.model_validate(job.item.payload)
    image = ""
    if payload.thumbnail:
        step = job.step("image")
        if step.get("status") == "done":
            image = str(step["urn"])
        else:
            await job.progress("uploading the card image", None)
            upload_url, image = await linkedin.initialize_image_upload()
            path = store.resolve_asset(job.site, payload.thumbnail)
            await linkedin.upload_image(
                upload_url, await asyncio.to_thread(path.read_bytes)
            )
            job.save("image", "done", urn=image)
    await job.progress("posting", None)
    urn = await _post_step(
        job,
        "post",
        "the LinkedIn post",
        lambda: linkedin.create_post(
            linkedin.little_escape(payload.text),
            link=payload.link,
            title=payload.title,
            description=payload.description,
            thumbnail=image,
        ),
    )
    return urn, linkedin.post_url(urn)


# --- YouTube ------------------------------------------------------------------------------


async def send_youtube(job: Job) -> tuple[str, str]:
    payload = social.YouTubePayload.model_validate(job.item.payload)
    step = job.step("upload")
    if step.get("status") == "done":
        video_id = str(step["video_id"])
        return video_id, youtube.video_url(video_id)
    path = store.resolve_asset(job.site, payload.video)
    size = path.stat().st_size
    mime = mimetypes.guess_type(path.name)[0] or "video/*"
    session = str(step.get("session") or "")
    video: dict[str, Any] | None = None
    if session:
        # Resuming: ask YouTube how much it already has, rather than trusting our count.
        offset, video = await youtube.query_offset(session, size)
    else:
        session = await youtube.start_upload(
            social.youtube_metadata(payload), size, mime
        )
        offset = 0
        job.save("upload", "uploading", session=session, offset=0)
    while video is None:
        chunk = await _read(path, offset, YOUTUBE_CHUNK)
        offset, video = await youtube.upload_chunk(session, chunk, offset, size)
        job.save("upload", "uploading", session=session, offset=offset)
        await job.progress(f"uploading {payload.video}", min(1.0, offset / size))
    video_id = str(video.get("id") or "")
    job.save("upload", "done", session=session, offset=size, video_id=video_id)
    return video_id, youtube.video_url(video_id)


# --- dev.to and Hashnode ----------------------------------------------------------------
# One call each, and the call is the post: whatever the article shows is already on the
# published site (approval filled in the URLs), so there is nothing to upload.


async def send_devto(job: Job) -> tuple[str, str]:
    payload = social.DevtoPayload.model_validate(job.item.payload)
    article: dict[str, Any] = {
        "title": payload.title,
        "body_markdown": payload.body,
        "published": payload.published,
        "tags": payload.tags,
    }
    for key, value in (
        ("description", payload.description),
        ("main_image", payload.cover),
        ("canonical_url", payload.canonical_url),
        ("series", payload.series),
    ):
        if value.strip():
            article[key] = value.strip()
    await job.progress("posting the article", None)
    article_id = await _post_step(
        job, "post", "the dev.to article", lambda: devto.create_article(article)
    )
    return article_id, str(job.step("post").get("url") or "")


async def send_hashnode(job: Job) -> tuple[str, str]:
    payload = social.HashnodePayload.model_validate(job.item.payload)
    post: dict[str, Any] = {
        "title": payload.title,
        "contentMarkdown": payload.body,
        "tags": [
            {"slug": hashnode.tag_slug(t), "name": t}
            for t in payload.tags
            if hashnode.tag_slug(t)
        ],
    }
    if payload.subtitle.strip():
        post["subtitle"] = payload.subtitle.strip()
    if payload.canonical_url.strip():
        post["originalArticleURL"] = payload.canonical_url.strip()
    if payload.cover.strip():
        post["coverImageOptions"] = {"coverImageURL": payload.cover.strip()}
    await job.progress("publishing the article", None)
    post_id = await _post_step(
        job, "post", "the Hashnode article", lambda: hashnode.publish_post(post)
    )
    return post_id, str(job.step("post").get("url") or "")


SENDERS: dict[str, Callable[[Job], Awaitable[tuple[str, str]]]] = {
    "x": send_x,
    "linkedin": send_linkedin,
    "youtube": send_youtube,
    "devto": send_devto,
    "hashnode": send_hashnode,
}
