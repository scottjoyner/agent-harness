#!/usr/bin/env python3
"""Read-only admission prefilter: sufficient resources never authorize dispatch."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

GIB = 1024 ** 3
MIN_AVAILABLE_RAM = 8 * GIB
MIN_FREE_DISK = 15 * GIB
MIN_FREE_SWAP = 1 * GIB
MAX_LOAD_PER_CPU = 0.8


def assess(*, meminfo: str, free_disk: int, load1: float, cpus: int) -> dict:
    fields = {}
    for line in meminfo.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            tokens = value.strip().split()
            if tokens and tokens[0].isdigit():
                fields[key] = int(tokens[0]) * 1024
    available = fields.get("MemAvailable", 0)
    swap_total = fields.get("SwapTotal", 0)
    swap_free = fields.get("SwapFree", 0)
    checks = {
        "memory": available >= MIN_AVAILABLE_RAM,
        "disk": free_disk >= MIN_FREE_DISK,
        "swap": swap_total == 0 or swap_free >= MIN_FREE_SWAP,
        "load": cpus >= 1 and load1 / cpus <= MAX_LOAD_PER_CPU,
    }
    return {
        "schema": 1,
        "checks": checks,
        "resource_prefilter_passed": all(checks.values()),
        "available_ram_bytes": available,
        "free_disk_bytes": free_disk,
        "swap_total_bytes": swap_total,
        "swap_free_bytes": swap_free,
        "load1": load1,
        "cpu_count": cpus,
        "sandbox_verified": False,
        "credentials_verified": False,
        "agent_dispatch_authorized": False,
    }


def physical() -> dict:
    meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
    return assess(meminfo=meminfo, free_disk=shutil.disk_usage(Path.home()).free,
                  load1=os.getloadavg()[0], cpus=os.cpu_count() or 0)


if __name__ == "__main__":
    print(json.dumps(physical(), indent=2, sort_keys=True))
