"""Device-care extension: camera-grounded troubleshooting with one safe booking.

A user points a camera at a device (a router's LEDs, a washer's error code),
asks what it means, gets the fix steps, and books exactly one technician
visit -- even if they change their mind mid-sentence. The tools here are mock
services over a small local knowledge base (`kb.json`); the kernel that drives
them is the same one that runs the FDB-v3 benchmark.
"""

from prism_rt.devicecare.tools import DeviceCareToolset

__all__ = ["DeviceCareToolset"]
