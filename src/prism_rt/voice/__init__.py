"""LiveKit voice host for Janus.

Everything in this package imports `livekit-agents` and local speech models
(faster-whisper, Kokoro), which only exist in the `docker/fdb_v3` image. The
kernel, workers, tests and every other module never import it.

Run: `python -m prism_rt.voice.agent start`
"""
