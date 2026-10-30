"""Instagram reels: the child that reads one (frames, then the transcript), the
parent that keeps what it had when it runs out of time, the extractor's
sections, and the frames in the analysis call. Run via
`docker compose exec api pytest tests/test_reels.py` (the child uses ffmpeg
and the Whisper model in the image)."""

import base64
import contextlib
import io
import json
import logging
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from PIL import Image

from app.ai import analyze as analyze_module
from app.ai.analyze import analyze
from app.config import get_settings
from app.extractors import instagram, reel
from app.extractors.base import ExtractedContent
from app.extractors.instagram import InstagramExtractor

URL = "https://www.instagram.com/reel/DfH83beRoH0/"


def _reel(**values: Any) -> reel.Reel:
    return reel.Reel(
        **{
            "author": "Arjay McCandless",
            "handle": "arjay_the_dev",
            "caption": "Lean 4 vs $12 server?\n\nThis one was crazy ngl.",
            "thumbnail_url": "https://scontent.cdninstagram.com/cover.jpg",
            "frames": ["ZnJhbWU="],
            "transcript": "Somebody used mathematical proofs.",
            **values,
        }
    )


async def test_a_reel_becomes_sections_with_its_transcript_and_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budgets: list[float] = []

    async def fetch(url: str, timeout_s: float) -> reel.Reel:
        budgets.append(timeout_s)
        return _reel()

    monkeypatch.setattr(reel, "fetch", fetch)
    c = await InstagramExtractor().extract(URL, share_text="the lean one")
    # Two thirds of the extraction's time, leaving the rest for the post's page.
    assert budgets == [get_settings().extract_timeout_s * 2 / 3]
    assert c.raw_text == (
        "Author: Arjay McCandless (@arjay_the_dev)\n\n"
        "Description:\nLean 4 vs $12 server?\nThis one was crazy ngl.\n\n"
        "Transcript:\nSomebody used mathematical proofs.\n\n"
        "User note:\nthe lean one"
    )
    assert (c.title, c.author) == (
        "Reel by Arjay McCandless",
        "Arjay McCandless (@arjay_the_dev)",
    )
    assert c.thumbnail_url == "https://scontent.cdninstagram.com/cover.jpg"
    assert c.frames == ["ZnJhbWU="]
    # The frames show the video; the cover would only repeat one of them.
    assert "image_b64" not in c.extra


async def test_a_reel_without_speech_keeps_its_caption(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetch(url: str, timeout_s: float) -> reel.Reel:
        return _reel(author=None, transcript=None)

    monkeypatch.setattr(reel, "fetch", fetch)
    c = await InstagramExtractor().extract(URL)
    assert c.raw_text == (
        "Author: @arjay_the_dev\n\nDescription:\nLean 4 vs $12 server?\nThis one was crazy ngl."
    )
    assert c.title == "Reel by @arjay_the_dev"


async def test_a_post_without_a_video_is_read_from_its_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fetch(url: str, timeout_s: float) -> None:
        return None

    async def from_page(self: Any, url: str, share_text: str | None) -> ExtractedContent:
        return ExtractedContent(title="From the page")

    monkeypatch.setattr(reel, "fetch", fetch)
    monkeypatch.setattr(InstagramExtractor, "_from_page", from_page)
    assert (await InstagramExtractor().extract(URL)).title == "From the page"


async def test_a_posts_page_becomes_sections_free_of_blank_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = (
        '<meta property="og:title" content="Arjay on Instagram: &quot;Lean 4?\n\nCrazy.&quot;">'
        '<meta property="og:description" content="4,491 likes - &quot;Lean 4?\n\nCrazy.&quot;">'
        '<meta property="og:image" content="https://scontent.cdninstagram.com/cover.jpg">'
    )

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == "scontent.cdninstagram.com":
            return httpx.Response(200, content=b"jpeg")
        return httpx.Response(200, text=page)

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(answer), **kwargs)

    monkeypatch.setattr(instagram, "public_client", client)
    c = await InstagramExtractor()._from_page(URL, "the lean one\n\nsaved for later")
    # The reader splits sections at blank lines and takes only letters and
    # spaces for a section's name.
    assert c.raw_text == (
        'Title: Arjay on Instagram: "Lean 4?\nCrazy."\n\n'
        'Description: 4,491 likes - "Lean 4?\nCrazy."\n\n'
        "User note:\nthe lean one\nsaved for later"
    )
    assert c.extra["image_b64"] == base64.b64encode(b"jpeg").decode("ascii")


def _video(path: Path, size: str, picture_s: int = 4) -> Path:
    """Four seconds of a 440 Hz tone under `picture_s` of test pattern."""
    subprocess.run(
        [
            *("ffmpeg", "-v", "error", "-f", "lavfi", "-i"),
            f"testsrc=size={size}:rate=10:duration={picture_s}",
            *("-f", "lavfi", "-i", "sine=frequency=440:duration=4"),
            *("-pix_fmt", "yuv420p", str(path)),
        ],
        check=True,
    )
    return path


def _sizes(frames: list[str]) -> set[tuple[int, int]]:
    return {Image.open(io.BytesIO(base64.b64decode(f))).size for f in frames}


def test_the_child_writes_frames_and_a_transcript_beside_the_video(tmp_path: Path) -> None:
    info = {
        "uploader": "Arjay McCandless",
        "channel": "arjay_the_dev",
        "description": "Lean 4 vs $12 server?",
        "thumbnail": "https://scontent.cdninstagram.com/cover.jpg",
    }
    reel.describe(_video(tmp_path / "video.mp4", "720x1280"), info, tmp_path)
    got = reel.read(tmp_path)
    assert got is not None
    assert (got.author, got.handle, got.caption, got.thumbnail_url) == (
        "Arjay McCandless",
        "arjay_the_dev",
        "Lean 4 vs $12 server?",
        "https://scontent.cdninstagram.com/cover.jpg",
    )
    # Frames fit in FRAME_PX either way up, and small ones aren't enlarged.
    assert len(got.frames) == reel.FRAMES
    assert _sizes(got.frames) == {(540, 960)}
    wide = tmp_path / "wide"
    wide.mkdir()
    reel.describe(_video(wide / "video.mp4", "1280x720"), {}, wide)
    assert _sizes(reel.read(wide).frames) == {(960, 540)}  # type: ignore[union-attr]
    small = tmp_path / "small"
    small.mkdir()
    reel.describe(_video(small / "video.mp4", "320x240"), {}, small)
    assert _sizes(reel.read(small).frames) == {(320, 240)}  # type: ignore[union-attr]
    # A tone has no speech in it: no transcript rather than made-up words.
    assert got.transcript is None


def test_a_picture_that_ends_before_the_sound_still_gives_its_frames(tmp_path: Path) -> None:
    # ffmpeg can't take a frame past the picture's end; those are left out
    # rather than losing the whole reel.
    reel.describe(_video(tmp_path / "video.mp4", "320x240", picture_s=1), {}, tmp_path)
    got = reel.read(tmp_path)
    assert got is not None
    assert 1 <= len(got.frames) < reel.FRAMES


def test_the_parent_reads_only_a_whole_reel_of_the_right_shape(tmp_path: Path) -> None:
    file = tmp_path / "reel.json"
    assert reel.read(tmp_path) is None
    file.write_text('{"frames": ["ZnJh')
    assert reel.read(tmp_path) is None
    # The child reads what anyone can post, so what it writes is checked: at
    # most FRAMES frames go on to Claude, and nothing of the wrong type.
    whole = asdict(_reel(frames=["ZnJhbWU="] * 20))
    file.write_text(json.dumps(whole))
    assert len(reel.read(tmp_path).frames) == reel.FRAMES  # type: ignore[union-attr]
    for wrong in ({"frames": [7]}, {"frames": "ZnJhbWU="}, {"caption": ["x"]}, {"extra": 1}):
        file.write_text(json.dumps(whole | wrong))
        assert reel.read(tmp_path) is None, wrong
    file.write_text("[]")
    assert reel.read(tmp_path) is None


def _running() -> int:
    """Children still running `python -m app.extractors.reel` (Linux: /proc)."""
    found = 0
    for proc in Path("/proc").glob("[0-9]*"):
        with contextlib.suppress(OSError):
            found += b"app.extractors.reel" in (proc / "cmdline").read_bytes()
    return found


async def test_the_child_gets_no_secrets_and_is_killed_when_it_takes_too_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    assert "ANTHROPIC_API_KEY" not in reel.child_env()
    # Only yt-dlp's Instagram extractor runs, so another site's link fails
    # without being fetched.
    assert await reel.fetch("https://example.com/reel/abc/", timeout_s=30) is None
    # Python alone takes longer than this to start: the child is killed.
    assert await reel.fetch(URL, timeout_s=0.01) is None
    assert _running() == 0


def _child(path: Path, script: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stands in for `python -m app.extractors.reel <url> <folder>`."""
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(path))


def _alive(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


async def test_a_child_out_of_time_still_gives_the_frames_it_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # It starts a helper (as yt-dlp starts ffmpeg), writes the frames, then
    # transcribes forever.
    partial = json.dumps(asdict(reel.Reel(None, "a", "cap", None, ["ZnJhbWU="], None)))
    helper = tmp_path / "helper.pid"
    script = (
        f"sleep 30 &\necho $! > {helper}\nprintf '%s' '{partial}' > \"$4/reel.json\"\nexec sleep 30"
    )
    _child(tmp_path / "child.sh", script, monkeypatch)
    got = await reel.fetch(URL, timeout_s=1)
    assert got == reel.Reel(None, "a", "cap", None, ["ZnJhbWU="], None)
    # The helper went with it.
    assert not _alive(int(helper.read_text()))


async def test_a_reel_that_fails_is_a_warning_but_a_post_without_video_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=reel.__name__)
    child = tmp_path / "child.sh"
    _child(child, "echo 'login required' >&2\nexit 1", monkeypatch)
    assert await reel.fetch(URL, timeout_s=5) is None
    _child(child, f"echo 'the post has no video' >&2\nexit {reel.NO_VIDEO}", monkeypatch)
    assert await reel.fetch(URL, timeout_s=5) is None
    assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == reel.__name__] == [
        (logging.WARNING, "reel: login required"),
        (logging.INFO, "reel: the post has no video"),
    ]


def _answer(**data: Any) -> SimpleNamespace:
    text = json.dumps(
        {"title": "T", "summary": "S", "tags": [], "people": [], "handles": []}
        | {"brands_products": [], "topics": []}
        | data
    )
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


async def test_frames_go_to_claude_and_their_on_screen_text_comes_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, Any] = {}

    async def claude(**params: Any) -> SimpleNamespace:
        sent.update(params)
        return _answer(on_screen="@hargup PR #153\n\nlean-derive-hargup 280,000")

    monkeypatch.setattr(analyze_module, "claude", claude)
    content = ExtractedContent(raw_text="Transcript:\nhi", frames=["AAAA", "BBBB"])
    got = await analyze(content, url=URL, source="instagram")
    blocks = sent["messages"][0]["content"]
    assert [b["type"] for b in blocks] == ["image", "image", "text"]
    assert [b["source"]["data"] for b in blocks[:2]] == ["AAAA", "BBBB"]
    assert '"on_screen"' in blocks[-1]["text"]
    # Kept as one section: no blank lines.
    assert got.on_screen == "@hargup PR #153\nlean-derive-hargup 280,000"

    # "Plain lines" sometimes come back as a list of them.
    async def as_list(**params: Any) -> SimpleNamespace:
        return _answer(on_screen=["@hargup PR #153", "", "lean-derive-hargup 280,000", 7])

    monkeypatch.setattr(analyze_module, "claude", as_list)
    got = await analyze(content, url=URL, source="instagram")
    assert got.on_screen == "@hargup PR #153\nlean-derive-hargup 280,000\n7"

    monkeypatch.setattr(analyze_module, "claude", claude)
    sent.clear()
    got = await analyze(ExtractedContent(raw_text="Body:\nhi"), url=URL, source="web")
    assert '"on_screen"' not in sent["messages"][0]["content"][-1]["text"]
    assert got.on_screen is None
