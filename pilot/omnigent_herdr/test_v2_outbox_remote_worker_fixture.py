"""Malformed inputs must never cause fixture to accept or persist authority."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from v2_outbox_remote_worker_fixture import run


class RemoteV2WorkerTests(unittest.TestCase):
    def _invoke(self, packets):
        with tempfile.TemporaryDirectory(prefix="v2-worker-safe-") as directory:
            root = Path(directory)
            (root / ".cache").mkdir()
            data = io.StringIO("".join(
                json.dumps(packet) + "\n" for packet in packets))
            sink = io.StringIO()
            with patch.object(Path, "home", return_value=root):
                run(stdin=data, stdout=sink)
            response = [json.loads(line) for line in sink.getvalue().splitlines()]
            self.assertFalse(list((root / ".cache").iterdir()))
            for item in response:
                self.assertFalse(item["agent_dispatch_authorized"])
                self.assertFalse(item["model_egress_authorized"])
                self.assertEqual(item["provider_requests_forwarded"], 0)
            return response

    def test_unsolicited_ack_denied(self):
        answers = self._invoke([{"op": "ack", "signed_ack": {}}])
        self.assertEqual([a["status"] for a in answers], ["denied"])

    def test_out_of_order_commit_denied(self):
        answers = self._invoke([{"op": "commit"}])
        self.assertEqual([a["status"] for a in answers], ["denied"])

    def test_invalid_observer_key_denied(self):
        answers = self._invoke([{
            "op": "init", "observer_public_key": "bad??",
            "observer_fingerprint": "a"*64,
        }])
        self.assertEqual([a["status"] for a in answers], ["denied"])

    def test_unexpected_extra_field_denied(self):
        answers = self._invoke([{
            "op": "init", "observer_public_key": "bad??",
            "observer_fingerprint": "a"*64, "shell": "true",
        }])
        self.assertEqual([a["status"] for a in answers], ["denied"])


if __name__ == "__main__":
    unittest.main()
