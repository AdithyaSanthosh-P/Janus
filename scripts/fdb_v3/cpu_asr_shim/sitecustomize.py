"""Site-import shim for FDB's own scorer process (loaded via PYTHONPATH by
scripts/fdb_v3/dev_livekit_run.sh for that one `docker exec` only).

FDB's run_tool_benchmark(_all_released).py loads a second ASR model (NeMo
Parakeet, fp32) purely to transcribe recorded audio for its report. On an
8 GB laptop GPU that shares the card with the live agent's Whisper+Kokoro,
it OOM'd -- but only at *load time*: NeMo restores the checkpoint with
`torch.load(..., map_location=<cuda>)` into a model that is already on the
GPU, briefly holding two full copies (~5 GB). Steady state is ~2.55 GB,
which fits.

So this shim redirects a CUDA `map_location` in `torch.load` to the CPU:
the state dict lands in host RAM, NeMo copies it into the GPU-resident
model once, and the peak VRAM is a single copy. Precision is unchanged
(fp32, same as the organizers' scoring machine). An earlier version of
this shim hid the GPU entirely and ran Parakeet on the CPU instead; that
moved ~5 GB into *system* RAM on a 14 GB machine and got the terminal
OOM-killed mid-run (2026-09-27), so it was replaced by this.
"""
import torch

_orig_torch_load = torch.load


def _is_cuda_location(loc) -> bool:
    if loc is None:
        return False
    if isinstance(loc, torch.device):
        return loc.type == "cuda"
    if isinstance(loc, str):
        return loc.startswith("cuda")
    return False


def _load_to_cpu_first(*args, **kwargs):
    if _is_cuda_location(kwargs.get("map_location")):
        kwargs["map_location"] = "cpu"
    return _orig_torch_load(*args, **kwargs)


torch.load = _load_to_cpu_first
