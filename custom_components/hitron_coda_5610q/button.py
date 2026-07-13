"""Button entities for the Hitron CODA-5610Q.

v0.2.14: removed the per-device pause/resume buttons.

Background: v0.2.12 introduced 42 button entities (21 pause + 21 resume,
one pair per connected device). The feature is rarely used — the router's
own web UI handles this better, and the buttons didn't generate unique
IDs that coexisted cleanly with the v0.2.13 device_tracker changes
(unique_id collision warnings in the HA log).

If you actually need to pause a device's internet access from HA, use
the integration's service call: hitron_coda_5610q.pause_device.
"""
from __future__ import annotations
