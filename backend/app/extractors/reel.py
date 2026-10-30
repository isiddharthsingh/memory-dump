"""Instagram reels, read in a child process: the video downloaded with yt-dlp,
FRAMES frames taken evenly through it, and what is said in its first
MAX_AUDIO_S transcribed by Whisper (WHISPER_MODEL, which the Dockerfile puts
in the image).

Anyone who can sign up picks the reels read here, so `fetch` reads each in a
child process (`python -m app.extractors.reel <url> <folder>`) that may use at
most MEMORY_CAP, gets none of the worker's environment (its keys), and is
killed after the time it is given, with the ffmpeg it and yt-dlp start. The
child writes what it has to reel.json in a folder the parent owns, its frames
first and then its transcript, so a reel that takes too long to transcribe
still gives its caption and frames; the parent checks what it wrote. yt-dlp
runs only its Instagram extractor, which talks only to Instagram.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import resource
import signal
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

FRAMES = 8
# Frames fit in a square this size: a phone's text stays legible, at ~700
# image tokens a frame.
FRAME_PX = 960
# Only the start of a long video is transcribed, so it fits in the time given.
MAX_AUDIO_S = 300
# The smallest video stream is downloaded: a bigger one is a long video.
MAX_BYTES = 50 << 20
# The child's exit status for a post without a video, such as a photo.
NO_VIDEO = 2
# Whisper base takes ~520 MB; yt-dlp and ffmpeg run under the same cap.
MEMORY_CAP = 2 << 30
# Multilingual and quick on a CPU (~16× real time); `small` hears better, in
# three times the time and twice the memory.
WHISPER_MODEL = "base"
# All the child's environment: enough for Python to find this module and
# Whisper its model.
_CHILD_ENV = ("PATH", "PYTHONPATH", "LANG", "LC_ALL", "HF_HOME", "HF_HUB_OFFLINE")
_FILE = "reel.json"


@dataclass
class Reel:
    author: str | None
    handle: str | None
    caption: str | None
    thumbnail_url: str | None
    # Base64 JPEGs, in order.
    frames: list[str]
    transcript: str | None


async def fetch(url: str, timeout_s: float) -> Reel | None:
    """The reel at `url`, or None when the post has no video or it can't be
    read; what the child had by `timeout_s` when it takes longer (see above)."""
    # A helper killed with the child may still be letting go of the folder.
    with tempfile.TemporaryDirectory(prefix="reel-", ignore_cleanup_errors=True) as folder:
        try:
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "app.extractors.reel",
                url,
                folder,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=child_env(),
                # A group of its own, so the ffmpeg it starts can be killed with it.
                start_new_session=True,
            )
        except OSError as exc:
            # Out of processes or file descriptors: the post's page is read instead.
            log.warning("reel: couldn't start reading it: %r", exc)
            return None
        try:
            async with asyncio.timeout(timeout_s):
                _, errors = await child.communicate()
            if child.returncode:
                reason = errors.decode(errors="replace").strip()[-500:]
                # A photo has no video. Anything else (Instagram asking to sign
                # in, Whisper failing) means reels are going unread.
                level = logging.INFO if child.returncode == NO_VIDEO else logging.WARNING
                log.log(level, "reel: %s", reason or f"exit {child.returncode}")
        except TimeoutError:
            log.warning("reel: reading it took over %ss", timeout_s)
        finally:
            # Also when the job is cancelled: never leave one reading.
            if child.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                await child.wait()
        return read(Path(folder))


def child_env() -> dict[str, str]:
    return {name: os.environ[name] for name in _CHILD_ENV if name in os.environ}


def read(folder: Path) -> Reel | None:
    """What the child wrote to `folder`, or None when it wrote nothing whole.
    The child reads what anyone can post, so only text goes on, and at most
    FRAMES frames: one taken over by a decoder bug can't send Claude more."""
    try:
        found = Reel(**json.loads((folder / _FILE).read_text()))
    except (OSError, ValueError, TypeError):
        return None
    texts = (found.author, found.handle, found.caption, found.thumbnail_url, found.transcript)
    if not all(text is None or isinstance(text, str) for text in texts):
        return None
    if not isinstance(found.frames, list) or not all(isinstance(f, str) for f in found.frames):
        return None
    found.frames = found.frames[:FRAMES]
    return found


def describe(video: Path, info: dict[str, Any], folder: Path) -> None:
    """Writes the reel to `folder`: its frames, then its transcript. Runs in
    the child; call `fetch`."""
    reel = Reel(
        author=info.get("uploader"),
        handle=info.get("channel"),
        caption=info.get("description"),
        thumbnail_url=info.get("thumbnail"),
        frames=_frames(video),
        transcript=None,
    )
    _write(folder, reel)
    reel.transcript = _transcript(video)
    if reel.transcript:
        _write(folder, reel)


def _write(folder: Path, reel: Reel) -> None:
    # Whole or not at all: the child may be killed while writing.
    part = folder / f"{_FILE}.part"
    part.write_text(json.dumps(asdict(reel)))
    part.replace(folder / _FILE)


def _download(url: str, folder: Path) -> tuple[Path, dict[str, Any]]:
    from yt_dlp import YoutubeDL  # type: ignore[import-untyped]

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # The smallest video with the best audio: frames are scaled down anyway.
        "format": "wv*+ba/w",
        "merge_output_format": "mp4",
        "outtmpl": str(folder / "video.%(ext)s"),
        "max_filesize": MAX_BYTES,
        # A carousel's first item.
        "playlist_items": "1",
        # A photo comes back without formats instead of as an error.
        "ignore_no_formats_error": True,
        "socket_timeout": 15,
        "cachedir": False,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, ie_key="Instagram") or {}
    if entries := list(info.get("entries") or []):
        info = {**info, **entries[0]}
    video = folder / "video.mp4"
    if not video.exists():
        raise _NoVideo("the post has no video")
    return video, dict(info)


class _NoVideo(LookupError):
    pass


def _frames(video: Path) -> list[str]:
    """Up to FRAMES frames, evenly through the video. Ones ffmpeg can't take
    are left out: the picture can end before the sound, whose length the
    container gives."""
    probe = subprocess.run(
        [
            *("ffprobe", "-v", "error", "-show_entries", "format=duration"),
            *("-of", "csv=p=0", str(video)),
        ],
        capture_output=True,
        text=True,
    )
    try:
        seconds = float(probe.stdout)
    except ValueError:
        # "N/A", or unreadable: the transcript may still be had.
        return []
    box = f"'min({FRAME_PX},iw)':'min({FRAME_PX},ih)'"
    frames: list[str] = []
    for i in range(FRAMES):
        try:
            jpeg = _ffmpeg(
                *("-ss", f"{seconds * (i + 0.5) / FRAMES:.2f}", "-i", str(video)),
                *("-frames:v", "1", "-q:v", "4", "-c:v", "mjpeg", "-f", "image2pipe"),
                *("-vf", f"scale={box}:force_original_aspect_ratio=decrease:force_divisible_by=2"),
            )
        except subprocess.CalledProcessError:
            continue
        if jpeg:
            frames.append(base64.b64encode(jpeg).decode("ascii"))
    return frames


def _transcript(video: Path) -> str | None:
    """What is said in the first MAX_AUDIO_S, or None when nothing is: music,
    silence, no sound at all."""
    import numpy as np
    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

    try:
        # Whisper opens files with PyAV, whose newer releases it can't call,
        # so it is handed the samples instead.
        pcm = _ffmpeg(
            *("-i", str(video), "-t", str(MAX_AUDIO_S), "-vn"),
            *("-ac", "1", "-ar", "16000", "-f", "f32le"),
        )
    except subprocess.CalledProcessError:
        return None
    if not pcm:
        return None
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    # The voice detector skips music and silence, where Whisper makes up words.
    segments, _ = model.transcribe(np.frombuffer(pcm, np.float32), vad_filter=True)
    return " ".join(" ".join(s.text for s in segments).split()) or None


def _ffmpeg(*args: str) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-v", "error", *args, "-"], capture_output=True, check=True
    ).stdout


def _main() -> None:
    """The child: `<url> <folder>`. Exits NO_VIDEO when the post has no video,
    or 1 when it can't be read, with the reason on stderr."""
    # Never above a hard limit the worker already runs under.
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    cap = MEMORY_CAP if hard == resource.RLIM_INFINITY else min(MEMORY_CAP, hard)
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    url, folder = sys.argv[1], Path(sys.argv[2])
    try:
        video, info = _download(url, folder)
        describe(video, info, folder)
    except _NoVideo as exc:
        sys.stderr.write(f"{exc}\n")
        sys.exit(NO_VIDEO)
    except Exception as exc:
        sys.exit(f"no reel: {exc}")


if __name__ == "__main__":
    _main()
