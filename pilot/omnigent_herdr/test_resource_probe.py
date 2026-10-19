import unittest
from resource_probe import GIB, assess


def memory(avail: int, swap_free: int, swap_total: int = 8) -> str:
    return (f"MemAvailable: {avail * GIB // 1024} kB\n"
            f"SwapTotal: {swap_total * GIB // 1024} kB\n"
            f"SwapFree: {swap_free * GIB // 1024} kB\n")


class ResourceTests(unittest.TestCase):
    def test_ample_resources_do_not_authorize_dispatch(self):
        r = assess(meminfo=memory(16, 6), free_disk=100*GIB, load1=0.5, cpus=8)
        self.assertTrue(r["resource_prefilter_passed"])
        self.assertFalse(r["agent_dispatch_authorized"])

    def test_destroyer_memory_shortfall(self):
        r = assess(meminfo=memory(3, 0), free_disk=74*GIB, load1=1.1, cpus=8)
        self.assertFalse(r["checks"]["memory"])
        self.assertFalse(r["checks"]["swap"])
        self.assertFalse(r["resource_prefilter_passed"])

    def test_busy_node_fail_closed(self):
        r = assess(meminfo=memory(64, 8), free_disk=200*GIB, load1=30, cpus=8)
        self.assertFalse(r["checks"]["load"])

    def test_missing_meminfo_fails(self):
        r = assess(meminfo="", free_disk=100*GIB, load1=0, cpus=4)
        self.assertFalse(r["checks"]["memory"])

    def test_swapless_host_allowed_with_memory(self):
        r = assess(meminfo=memory(12, 0, 0), free_disk=50*GIB, load1=0, cpus=8)
        self.assertTrue(r["resource_prefilter_passed"])


if __name__ == "__main__":
    unittest.main()
