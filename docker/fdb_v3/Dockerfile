# FDB-v3 runtime container (docs/fdb_v3_implementation_plan.md §8, §11,
# §14) -- pinned dependencies for the LiveKit voice path (Day 2+) and for
# FDB's own evaluation pipeline (Parakeet ASR scoring, the gpt-4o judge).
#
# FDB's own source and benchmark data are never vendored into this image
# (§11: "FDB is never vendored or modified") -- they're cloned/downloaded
# at container start by scripts/fdb_v3/fetch_fdb.sh, or mounted in from a
# host checkout for local development. This image only carries the
# pinned Python/OS dependencies FDB's README asks for install-once.
#
# FDB's README pins with `~=1.3` / `~=1.0`, which float to the newest 1.x
# release -- we pin exact versions instead so a re-run months from now
# still matches what was tested (§8's stated reason).
#
# Base: an NVIDIA CUDA runtime image, matching the guide's stated
# evaluation environment ("single 48 GB GPU, NVIDIA, CUDA 12.x/13.x",
# guidelines/Theme05_Participant_Guide.md §5) and this machine's own
# driver (CUDA UMD 13.3, RTX 4060 Laptop 8GB, checked at build time).
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Ubuntu 22.04's own "jammy" universe python3.11 package is a frozen
# release-candidate build (3.11.0~rc1) -- found live: it's missing a
# stdlib attribute (sys.get_int_max_str_digits) modern PyTorch's
# _dynamo module imports at load time, so `import torch` alone crashed
# with an AttributeError. The deadsnakes PPA carries a real final
# Python 3.11 release build.
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common ca-certificates \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3.11-distutils python3-pip \
        ffmpeg git curl \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3.11 /usr/bin/python3 && \
    ln -sf /usr/bin/python3.11 /usr/bin/python

WORKDIR /fdb

# PyTorch pinned to its CUDA 12.6 build, installed before everything that
# depends on it (nemo_toolkit, kokoro, lightning) so none of them pulls the
# default PyPI wheel -- which is the CUDA 13 build and needs an NVIDIA driver
# >= 580. The reproduction target promises "CUDA 12.x/13.x": a CUDA 12 build
# runs on any driver from the 525 series up (CUDA minor-version
# compatibility), including 580+, so the same image works on both. Same
# torch version as before (2.14.0), only the CUDA build differs.
#
# TORCH_LOCAL_WHEELS=1 installs the same pinned wheels from
# docker/fdb_v3/wheels/ with no network (for a build host whose connection
# can't finish the ~3 GB download in one go); the default downloads them.
ARG TORCH_LOCAL_WHEELS=0
COPY docker/fdb_v3/wheels/ /tmp/wheels/
RUN if [ "$TORCH_LOCAL_WHEELS" = "1" ]; then \
        pip install --no-cache-dir --no-index --find-links /tmp/wheels "torch==2.14.0"; \
    else \
        pip install --no-cache-dir --timeout 120 --retries 10 \
            "torch==2.14.0" --index-url https://download.pytorch.org/whl/cu126; \
    fi && rm -rf /tmp/wheels

# Pinned per docs/fdb_v3_implementation_plan.md §8. §8 itself recorded
# livekit==1.1.20 from an earlier session's `~=1.0` resolve, taken
# independently of livekit-agents's own exact pin -- building this image
# for real found that livekit-agents==1.8.3 requires livekit==1.1.18
# exactly (pip's resolver: "livekit-agents[...] 1.8.3 depends on
# livekit==1.1.18"), so 1.1.20 is a real, previously-unverified pin
# error, not an environment issue. Corrected here. The last six were
# unpinned until 2 Oct (the openai SDK drives hosted speech-to-text and
# FDB's judge); pinned to the versions this same step resolved in the
# measured images.
RUN pip install --no-cache-dir \
        "livekit-agents[openai,google,xai]==1.8.3" \
        "livekit-plugins-ultravox==1.8.3" \
        "livekit-plugins-silero==1.8.3" \
        "livekit-plugins-openai==1.8.3" \
        "livekit[crypto]==1.1.18" \
        "nemo_toolkit[asr]==3.0.0" \
        "openai==2.54.0" \
        "pydub==0.25.1" "ffmpeg-python==0.2.0" "numpy==2.4.6" "python-dotenv==1.2.3" "gdown==6.4.1"

# Local speech stack for the Janus LiveKit worker: faster-whisper (STT) and
# Kokoro-82M (TTS). A separate layer after the large pip step above, so
# changing it never re-downloads that one. Versions are the ones already
# verified working together with torch 2.14 on this project's GPU.
# CTranslate2 (faster-whisper) is built for CUDA 12 + cuDNN 9: it uses the
# CUDA 12.4 cuBLAS and cuDNN 9 that this base image already ships, while
# torch keeps its own bundled CUDA 13 libraries (different sonames, no
# clash). Kokoro's out-of-vocabulary fallback needs espeak-ng; the
# `espeakng-loader` wheel misaki pulls in bundles the library, so no apt
# package is needed.
RUN pip install --no-cache-dir --timeout 120 --retries 10 \
        "faster-whisper==1.2.1" "ctranslate2==4.8.2" \
        "kokoro==0.9.4" "misaki[en]==0.9.4" "spacy==3.8.16" "soundfile==0.14.0" \
    && python -m spacy download en_core_web_sm

# Janus itself -- the actual agent under test runs inside this
# container in Day 2+ (LiveKit voice worker); T3/T4 offline harnesses
# (scripts/fdb_v3/) also run from here so the whole pipeline is
# reproducible from one pinned environment (§14, one-command repro).
# `pip install /janus` (building its own pyproject.toml-based wheel) is
# deliberately *not* done here -- found live: the huge nemo_toolkit
# dependency chain above pulls in a very new setuptools (84.x) that's
# incompatible with Ubuntu/deadsnakes' patched distutils for a legacy
# setup-based build ("AttributeError: install_layout"), and it's
# unnecessary anyway -- Janus's only runtime deps (pydantic, PyYAML,
# pyproject.toml) are already satisfied by this environment's existing
# package set (pydantic/pyyaml both already installed above, as
# dependencies of nemo_toolkit/livekit-agents themselves). PYTHONPATH
# below makes `prism_rt` importable directly from source, the same way
# `PYTHONPATH=src:.` works for local development (see CLAUDE.md).
#
# Model weights are baked in at pinned revisions (Whisper, Kokoro, and FDB's
# own Parakeet scoring ASR), so the worker's 300 s prewarm and the benchmark
# never wait on a download. Only speech.py (the pins) and the prefetch script
# are copied first, so ordinary code changes below never re-download ~2.5 GB.
ENV HF_HOME=/root/.cache/huggingface
COPY src/prism_rt/voice/speech.py /tmp/janus_speech.py
COPY scripts/fdb_v3/prefetch_models.py /tmp/prefetch_models.py
RUN JANUS_SPEECH_PY=/tmp/janus_speech.py python /tmp/prefetch_models.py \
    && rm /tmp/janus_speech.py /tmp/prefetch_models.py

COPY pyproject.toml /janus/pyproject.toml
COPY src /janus/src
COPY config /janus/config
COPY scripts /janus/scripts

ENV PYTHONPATH=/janus/src:/janus

# FDB is cloned/downloaded at run time, not baked into the image -- see
# scripts/fdb_v3/fetch_fdb.sh (verifies the pinned commit and the data zip's
# sha256 before anything runs against it). reproduce.sh at the repo root
# drives everything; with no arguments this image opens a shell, as before.
WORKDIR /janus
ENTRYPOINT ["/bin/bash"]
