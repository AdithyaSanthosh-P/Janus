"""Reads an appliance's error code off the camera, for the evidence watch
(adapters/world_watch.py, kernel/evidence.py). Demo mode only.

While a technician visit offered for the washer's error waits for a yes, the
watch asks what the washer shows now. With a fresh camera frame, the answer is
what the camera sees: the code on the display ("4C"), or "clear" when the
display is in view and shows no error code. A frame where no display can be
made out is no news (None): pointing the phone away is not a cleared error.
Without a fresh frame (no camera, or the camera is off), the simulated
washer's own status answers, so the watch still has a baseline.

`complete_json` is the model provider's (`workers/gateway.py`), called off the
event loop; the reading is one short vision call per watch interval.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Callable

log = logging.getLogger("janus.display_reader")

CLEAR = "clear"

PROMPT = (
    "The image is a photo of a home appliance, for example a washing machine. Look at its display panel. "
    "Is an error code shown on the display right now? An error code is a short code of letters and digits "
    "such as 4C, 5C, UE, dE, OE, HE or LE, often blinking or shown alone. A remaining time (like 1:05), a "
    "temperature, a spin speed, a program name, or a dark or blank display is NOT an error code. "
    "Set display_visible to false when no appliance display can be made out in the image."
)
SCHEMA = {
    "type": "object",
    "properties": {"display_visible": {"type": "boolean"}, "error_code": {"type": ["string", "null"]}},
    "required": ["display_visible", "error_code"],
}


def reading(answer) -> str | None:
    """The watch value for a model answer: the code, "clear", or None (no news)."""
    if not isinstance(answer, dict) or answer.get("display_visible") is not True:
        return None
    code = answer.get("error_code")
    if code is None or not str(code).strip() or str(code).strip().lower() in ("none", "null", "no", "n/a"):
        return CLEAR
    code = re.sub(r"\s+", "", str(code).strip())
    return code.upper() if len(code) <= 4 else None  # anything longer is not a code the washer shows


class DisplayReader:
    def __init__(
        self,
        complete_json: Callable[..., dict],
        latest_frame: Callable[[float], object | None],
        fallback: Callable[[str], str | None],
        *,
        max_age_s: float = 4.0,
    ) -> None:
        self._complete_json = complete_json
        self._latest_frame = latest_frame
        self._fallback = fallback
        self._max_age_s = max_age_s

    async def read(self, device: str) -> str | None:
        seen, value = await self.look(device)
        if not seen and self._latest_frame(self._max_age_s) is None:
            return self._fallback(device)
        return value

    async def look(self, device: str) -> tuple[bool, str | None]:
        """What the camera alone shows, for the washer offer: (False, None)
        without a fresh frame or when the read fails, else (True, reading):
        the code, "clear", or None when no display can be made out."""
        frame = self._latest_frame(self._max_age_s)
        if frame is None:
            return False, None
        try:
            answer = await asyncio.to_thread(self._complete_json, "display", PROMPT, SCHEMA, media=[frame])
        except Exception as exc:  # noqa: BLE001 - a failed read is no news
            log.info("display read failed: %s", exc)
            return False, None
        value = reading(answer)
        log.info("display read for %s: %s -> %s", device, answer, value)
        return True, value
