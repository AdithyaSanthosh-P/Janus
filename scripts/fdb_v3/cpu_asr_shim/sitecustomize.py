"""Site-import shim: on a CUDA-less process, nn.Module.cuda() becomes a no-op.

FDB's own run_tool_benchmark(_all_released).py unconditionally calls
model.cuda() when loading its NeMo Parakeet scoring model (every nn.Module
has a `.cuda` attribute, so its own `hasattr(model, "cuda")` check never
actually protects anything) -- there is no CPU-only code path in the eval
kit as shipped. This process is launched with CUDA_VISIBLE_DEVICES="" so the
offline scorer's ~5GB Parakeet model doesn't compete with the live agent's
own GPU-resident Whisper/Kokoro for VRAM on an 8GB laptop GPU. Without this
shim, `model.cuda()` still raises even with no GPU visible.

Only takes effect when CUDA genuinely isn't visible to this process, so it
is a no-op everywhere else (e.g. running the same scorer on a GPU with
enough VRAM to hold both models at once).
"""
import torch

if not torch.cuda.is_available():
    _noop_cuda = lambda self, *a, **k: self  # noqa: E731
    torch.nn.Module.cuda = _noop_cuda
    # NeMo's ASRModel actually inherits its .cuda() from PyTorch Lightning's
    # own mixin (lightning.fabric...DeviceDtypeModuleMixin), which shadows
    # nn.Module's version in the MRO -- patching only nn.Module.cuda above
    # never gets called for it.
    try:
        import lightning.fabric.utilities.device_dtype_mixin as _ddm

        _ddm._DeviceDtypeModuleMixin.cuda = _noop_cuda
    except (ImportError, AttributeError):
        pass
