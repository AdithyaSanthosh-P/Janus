"""Camera frames for the device-care extension.

`FrameStore` keeps the last few JPEG frames the worker has emitted and is the
`blob_resolver` the VISION worker reads bytes through (the kernel only ever
sees the `frame_id`). `FrameSampler` decides when a frame is worth emitting:
about one per second, and never faster, so a 30 fps track does not flood the
kernel with observations.

Both are plain Python (no LiveKit import) so they are unit-tested directly;
`pump_video_track` is the only LiveKit-facing piece and is imported lazily.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Awaitable, Callable

from prism_rt.workers.gateway import MediaPart

logger = logging.getLogger("janus.voice.camera")


class FrameStore:
    def __init__(self, keep: int = 8) -> None:
        self._keep = keep
        self._frames: "OrderedDict[str, MediaPart]" = OrderedDict()
        self._n = 0

    def put(self, data: bytes, mime_type: str = "image/jpeg") -> str:
        self._n += 1
        frame_id = f"cam-{self._n}"
        self._frames[frame_id] = MediaPart(mime_type=mime_type, data=data)
        while len(self._frames) > self._keep:
            self._frames.popitem(last=False)
        return frame_id

    def get(self, frame_id: str) -> MediaPart | None:
        return self._frames.get(frame_id)

    __call__ = get  # usable directly as a blob_resolver


class FrameSampler:
    def __init__(self, interval_s: float = 1.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._interval = interval_s
        self._clock = clock
        self._last: float | None = None

    def due(self) -> bool:
        now = self._clock()
        if self._last is not None and now - self._last < self._interval:
            return False
        self._last = now
        return True


async def pump_video_track(
    track,
    frames: FrameStore,
    emit: Callable[[str], Awaitable[None]],
    *,
    interval_s: float = 1.0,
    max_side: int = 960,
) -> None:
    """Sample `track` (a LiveKit remote video track) into `frames`, calling
    `emit(frame_id)` for each stored frame."""
    from livekit import rtc
    from livekit.agents.utils import images

    options = images.EncodeOptions(
        format="JPEG",
        quality=80,
        resize_options=images.ResizeOptions(width=max_side, height=max_side, strategy="scale_aspect_fit"),
    )
    sampler = FrameSampler(interval_s)
    stream = rtc.VideoStream(track)
    try:
        async for event in stream:
            if not sampler.due():
                continue
            try:
                data = await asyncio.to_thread(images.encode, event.frame, options)
            except Exception:  # noqa: BLE001 - one bad frame must not end the stream
                logger.exception("could not encode a camera frame")
                continue
            await emit(frames.put(data))
    finally:
        await stream.aclose()
