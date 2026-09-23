#!/usr/bin/env python3
"""Measure a vision-language model on camera frames before trusting it.

    python tools/vision_bench.py                                  # pinned model
    python tools/vision_bench.py --model Qwen/Qwen2-VL-2B-Instruct
    python tools/vision_bench.py --frames frames/pub_07_f017.png --variants

For each frame (and, with --variants, crops and a mirror of it, as a cheap
robustness check against a model that only matches one exact image), prints
what the model read, the search query DUET would send, which manual page the
kit's manual search would rank first for that query, time per frame, and
peak GPU memory.

The point of the manual column: the checkpoint in visual scenarios is decided
by whether the query names the component, because the mock ranks pages by
keyword hits. A reading is only as good as the page it retrieves.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from typing import List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _variants(path: str, tmp: str) -> List[str]:
    from PIL import Image, ImageOps
    out = [path]
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        crops = {
            "center70": (int(w * .15), int(h * .15), int(w * .85), int(h * .85)),
            "lowerhalf": (0, h // 3, w, h),
        }
        for name, box in crops.items():
            dst = os.path.join(tmp, name + "_" + os.path.basename(path))
            im.crop(box).save(dst)
            out.append(dst)
        dst = os.path.join(tmp, "mirror_" + os.path.basename(path))
        ImageOps.mirror(im).save(dst)
        out.append(dst)
    return out


def _top_page(query: str) -> str:
    from harness.mock_env import MockEnvironment
    env = MockEnvironment("vision_bench")
    result = env._default_result("lookup_manual", {"query": query, "image_embedding": [0.0]})
    pages = result.get("pages") or []
    return (pages[0]["title"] + " p" + str(pages[0]["page"])) if pages else "(no pages)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="", help="HF repo id (default: pinned VLM)")
    ap.add_argument("--revision", default="")
    ap.add_argument("--frames", nargs="*",
                    default=[os.path.join(ROOT, "frames", "pub_07_f017.png")])
    ap.add_argument("--variants", action="store_true")
    ap.add_argument("--question", default="What is this port used for?")
    args = ap.parse_args()

    if args.model:
        os.environ["DUET_VLM_MODEL"] = args.model
        if args.revision:
            os.environ["DUET_VLM_REVISION"] = args.revision
    os.environ.setdefault("DUET_VLM_ON_CPU", "1")

    from duet import telemetry
    from duet.perception.vision import VlmVision

    backend = VlmVision()
    started = time.monotonic()
    ok = asyncio.run(backend.warm())
    load_s = time.monotonic() - started
    if not ok:
        for record in telemetry.records():
            if str(record.get("event", "")).startswith("vision"):
                print(record)
        print("model failed to load")
        return 1
    print("model %s loaded in %.1fs" % (backend.model_name, load_s))

    try:
        import torch
        cuda = torch.cuda.is_available()
        if cuda:
            torch.cuda.reset_peak_memory_stats()
    except Exception:  # noqa: BLE001
        cuda = False

    tmp = os.path.join(ROOT, "generated", "vision_bench")
    os.makedirs(tmp, exist_ok=True)
    frames: List[str] = []
    for frame in args.frames:
        frames.extend(_variants(frame, tmp) if args.variants else [frame])

    for frame in frames:
        started = time.monotonic()
        reading = asyncio.run(backend.read_frame(frame, None))
        took = time.monotonic() - started
        query = (args.question + " " + reading.query).strip()
        print("\n%s  (%.2fs)" % (os.path.basename(frame), took))
        print("  focus      : %s   [confidence %.2f]" % (reading.focus, reading.confidence))
        print("  labels     : %s" % (reading.labels,))
        print("  query      : %s" % query)
        print("  manual top : %s" % _top_page(query))
        print("  raw        : %s" % reading.summary)
    if cuda:
        print("\npeak GPU memory: %.2f GB" % (torch.cuda.max_memory_allocated() / 1e9))
    return 0


if __name__ == "__main__":
    sys.exit(main())
