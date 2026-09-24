#!/usr/bin/env python3
"""Download every model DUET uses, at its pinned revision, into the HF cache.

    python tools/prefetch.py              # everything
    python tools/prefetch.py --cpu-only   # skip the GPU-only checkpoints

The organizers confirmed download time is not charged to setup(), and that
huggingface.co is reachable during evaluation. Running this first anyway
makes cold start predictable, lets the Docker image carry the weights, and
turns "the repository moved" into an error we see now rather than a silent
degradation after the deadline.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from duet.perception import checkpoints  # noqa: E402

GPU_ONLY = ("ASR_GPU", "VLM")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cpu-only", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="also fetch models that are disabled by configuration")
    args = ap.parse_args()

    from huggingface_hub import snapshot_download
    from duet import config

    failures = 0
    for role, _pinned in sorted(checkpoints.all_pinned().items()):
        if args.cpu_only and role in GPU_ONLY:
            print("skip   %-8s (GPU only)" % role)
            continue
        if role == "VLM" and not (config.VISION_MODEL_ENABLED or args.all):
            # 7.5 GB for a model the agent will not load (config.py explains).
            print("skip   %-8s (vision model disabled; --all to fetch)" % role)
            continue
        ckpt = checkpoints.get(role)
        started = time.monotonic()
        try:
            path = snapshot_download(ckpt.repo, revision=ckpt.revision)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print("FAILED %-8s %s@%s: %s" % (role, ckpt.repo, ckpt.revision,
                                             type(exc).__name__ + ": " + str(exc)[:200]))
            continue
        print("ok     %-8s %s@%s  (%.1fs)  %s" % (role, ckpt.repo, (ckpt.revision or "main")[:10],
                                                  time.monotonic() - started, path))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
