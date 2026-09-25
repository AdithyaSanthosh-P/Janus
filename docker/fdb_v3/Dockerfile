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

# Pinned per docs/fdb_v3_implementation_plan.md §8. §8 itself recorded
# livekit==1.1.20 from an earlier session's `~=1.0` resolve, taken
# independently of livekit-agents's own exact pin -- building this image
# for real found that livekit-agents==1.8.3 requires livekit==1.1.18
# exactly (pip's resolver: "livekit-agents[...] 1.8.3 depends on
# livekit==1.1.18"), so 1.1.20 is a real, previously-unverified pin
# error, not an environment issue. Corrected here.
RUN pip install --no-cache-dir \
        "livekit-agents[openai,google,xai]==1.8.3" \
        "livekit-plugins-ultravox==1.8.3" \
        "livekit-plugins-silero==1.8.3" \
        "livekit-plugins-openai==1.8.3" \
        "livekit[crypto]==1.1.18" \
        "nemo_toolkit[asr]==3.0.0" \
        "openai" \
        "pydub" "ffmpeg-python" "numpy" "python-dotenv" "gdown"

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
COPY pyproject.toml /janus/pyproject.toml
COPY src /janus/src
COPY config /janus/config
COPY scripts /janus/scripts

ENV PYTHONPATH=/janus/src:/janus

# FDB is cloned/downloaded at container start, not baked into the
# image -- see scripts/fdb_v3/fetch_fdb.sh (verifies the pinned commit
# and the data zip's sha256 before anything runs against it).
ENTRYPOINT ["/bin/bash"]
