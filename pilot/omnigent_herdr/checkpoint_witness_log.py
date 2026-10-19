#!/usr/bin/env python3
"""Cooperative append-only SYNTHETIC Ed25519 checkpoint witness log.

Separate from the worker lease DB and observer ACK DB. Requires an
externally supplied trusted (generation, record SHA-256) anchor when
reopening or appending. The *fixture* holds that anchor in RAM; the
module CANNOT establish independent administrative custody, immutable
storage, or freshness if the caller provides a rolled-back anchor.
No model inference, agent dispatch, provider egress or credentials.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import fcntl
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from capability_gate import _encode, _decode
from observer_checkpoint import (
    CHECKPOINT_DOMAIN, ZERO, REQUIRED, _json_bytes,
    checkpoint_sha256, public_fingerprint,
)

DOMAIN = b"herdr-synthetic-observer-witness-v1\0"
MAX_LOG_BYTES = 4 * 1024 * 1024
MAX_LINE_BYTES = 4096
MAX_RECORDS = 2048


class UnsafeWitness(ValueError):
    """Missing, rewritten, noncanonical, stale or untrusted witness log."""


def _sha(value: dict) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


class CheckpointWitnessLog:
    """One regular 0600 file in a private 0700 directory.

    A pinned observer PUBLIC key is provided by the verifier. Only a
    fixture-local PRIVATE key may append. The log is cooperatively
    append-only, NOT protected against privileged filesystem rollback.
    """

    def __init__(self, path: Path, *, observer_public_key: Ed25519PublicKey,
                 observer_id: str, worker_id: str,
                 signer: Ed25519PrivateKey | None = None):
        self.path = Path(path)
        self.public_key = observer_public_key
        self.fingerprint = public_fingerprint(observer_public_key)
        self.observer_id = observer_id
        self.worker_id = worker_id
        self.signer = signer
        if (not isinstance(observer_id, str)
                or not isinstance(worker_id, str)
                or observer_id == worker_id
                or not observer_id.isascii() or not worker_id.isascii()
                or len(observer_id) > 96 or len(worker_id) > 96
                or not all(c.isalnum() or c in "._-" for c in observer_id)
                or not all(c.isalnum() or c in "._-" for c in worker_id)
                or not observer_id or not worker_id):
            raise UnsafeWitness("invalid synthetic role identity")
        if signer is not None and public_fingerprint(signer.public_key()) != self.fingerprint:
            raise UnsafeWitness("observer signer differs from pinned public key")

    def _file_descriptor(self, *, create=False, write=False) -> int:
        path = self.path
        if not path.is_absolute() or path.name in ("", ".", ".."):
            raise UnsafeWitness("witness path must be absolute")
        root = path.parent
        if root.is_symlink() or not root.is_dir():
            raise UnsafeWitness("witness parent not a real directory")
        info = root.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise UnsafeWitness("witness parent must be owner-private")
        if create and not write:
            raise UnsafeWitness("creation requires publisher authority")
        flags = (os.O_RDWR if write else os.O_RDONLY) | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(path, flags, 0o600)
        except OSError as exc:
            raise UnsafeWitness("witness must exist or be newly created") from exc
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                    or st.st_uid != os.getuid() or st.st_mode & 0o077):
                raise UnsafeWitness("witness must be private regular file")
            fcntl.flock(fd, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _decode_checkpoint(self, cp: object, *, generation: int,
                           prior_checkpoint_sha256: str) -> None:
        if (not isinstance(cp, dict)
                or set(cp) != {"algorithm", "payload", "signature"}
                or cp["algorithm"] != "Ed25519"
                or not isinstance(cp["payload"], dict)
                or set(cp["payload"]) != REQUIRED):
            raise UnsafeWitness("invalid checkpoint envelope")
        p = cp["payload"]
        if (type(p["schema"]) is not int or p["schema"] != 1
                or p["kind"] != "synthetic_sqlite_state_checkpoint"
                or p["observer_id"] != self.observer_id
                or p["worker_id"] != self.worker_id
                or type(p["generation"]) is not int or p["generation"] != generation
                or type(p["created_unix"]) is not int or p["created_unix"] <= 0
                or p["previous_checkpoint_sha256"] != prior_checkpoint_sha256
                or not isinstance(p["state_sha256"], str)
                or len(p["state_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in p["state_sha256"])):
            raise UnsafeWitness("checkpoint scope, chain or generation mismatch")
        try:
            self.public_key.verify(
                _decode(cp["signature"]),
                CHECKPOINT_DOMAIN + _json_bytes(p))
        except (ValueError, TypeError, InvalidSignature, UnicodeError) as exc:
            raise UnsafeWitness("invalid checkpoint signature") from exc

    def _parse(self, fd: int) -> list[dict]:
        st0 = os.fstat(fd)
        if st0.st_size > MAX_LOG_BYTES:
            raise UnsafeWitness("witness log exceeds size cap")
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, MAX_LOG_BYTES + 1)
        st1 = os.fstat(fd)
        if (len(raw) != st0.st_size or len(raw) > MAX_LOG_BYTES
                or (st0.st_size, st0.st_mtime_ns, st0.st_ctime_ns)
                   != (st1.st_size, st1.st_mtime_ns, st1.st_ctime_ns)):
            raise UnsafeWitness("witness changed during read")
        if not raw:
            return []
        if not raw.endswith(b"\n"):
            raise UnsafeWitness("interrupted or truncated witness tail")
        lines = raw.splitlines(keepends=True)
        if len(lines) > MAX_RECORDS:
            raise UnsafeWitness("too many witness records")
        verified = []
        old_record_sha = ZERO
        old_cp_sha = ZERO
        for n, line in enumerate(lines, 1):
            if len(line) > MAX_LINE_BYTES:
                raise UnsafeWitness("oversized witness record")
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError) as exc:
                raise UnsafeWitness("invalid witness JSON") from exc
            if (not isinstance(record, dict)
                    or set(record) != {"schema", "kind", "generation",
                                      "previous_record_sha256", "checkpoint",
                                      "observer_fingerprint", "signature"}
                    or type(record["schema"]) is not int
                    or record["schema"] != 1
                    or record["kind"] != "synthetic_observer_highwater"
                    or type(record["generation"]) is not int
                    or record["generation"] != n
                    or record["previous_record_sha256"] != old_record_sha
                    or record["observer_fingerprint"] != self.fingerprint):
                raise UnsafeWitness("untrusted witness record or chain")
            if line != _json_bytes(record) + b"\n":
                raise UnsafeWitness("noncanonical or duplicate-key witness record")
            cp = record["checkpoint"]
            self._decode_checkpoint(
                cp, generation=n, prior_checkpoint_sha256=old_cp_sha)
            payload = {k: v for k, v in record.items() if k != "signature"}
            try:
                self.public_key.verify(
                    _decode(record["signature"]),
                    DOMAIN + _json_bytes(payload))
            except (ValueError, TypeError, InvalidSignature, UnicodeError) as exc:
                raise UnsafeWitness("invalid witness record signature") from exc
            old_record_sha = _sha(record)
            old_cp_sha = checkpoint_sha256(cp)
            verified.append(record)
        return verified

    @staticmethod
    def _anchor(rows: list[dict]) -> tuple[int, str]:
        if not rows:
            raise UnsafeWitness("no published checkpoint")
        return len(rows), _sha(rows[-1])

    def _require_anchor(self, rows: list[dict],
                        trusted_anchor: tuple[int, str] | None) -> None:
        if (not isinstance(trusted_anchor, tuple)
                or len(trusted_anchor) != 2
                or type(trusted_anchor[0]) is not int
                or not isinstance(trusted_anchor[1], str)
                or trusted_anchor[0] < 1
                or trusted_anchor[0] > len(rows)
                or _sha(rows[trusted_anchor[0] - 1]) != trusted_anchor[1]):
            raise UnsafeWitness("missing, unknown or rolled-back external anchor")

    def inspect(self, *, trusted_anchor: tuple[int, str]) -> dict:
        """Verify entire log and externally pinned historical record."""
        fd = self._file_descriptor()
        try:
            rows = self._parse(fd)
            self._require_anchor(rows, trusted_anchor)
            latest = rows[-1]["checkpoint"]
            return {
                "schema": 1, "records": len(rows),
                "anchor_generation": trusted_anchor[0],
                "latest_generation": len(rows),
                "latest_state_sha256": latest["payload"]["state_sha256"],
                "latest_checkpoint_sha256": checkpoint_sha256(latest),
                "latest_record_sha256": _sha(rows[-1]),
                "external_custody_verified": False,
                "rollback_resistance_verified": False,
                "agent_dispatch_authorized": False,
                "decision": "HOLD",
            }
        finally:
            os.close(fd)

    def publish(self, checkpoint: dict, *,
                trusted_anchor: tuple[int, str] | None,
                genesis=False) -> tuple[int, str]:
        """Append one signed checkpoint; caller owns and updates its anchor.

        There is no automatic rollback-repair or release-on-timeout.
        """
        if self.signer is None:
            raise UnsafeWitness("read-only verifier cannot publish")
        fd = self._file_descriptor(create=genesis, write=True)
        try:
            records = self._parse(fd)
            if genesis:
                if records or trusted_anchor is not None:
                    raise UnsafeWitness("genesis requires empty new file")
            else:
                self._require_anchor(records, trusted_anchor)
                # A stale-but-valid lower anchor may inspect history, but
                # MUST NOT be allowed to publish another transition.
                # If fsync succeeded but publication of the new anchor
                # failed, fence until the newer head is independently
                # reconciled; never guess the next generation.
                if trusted_anchor != self._anchor(records):
                    raise UnsafeWitness("stale writer high-water; reconciliation required")
            n = len(records) + 1
            if n > MAX_RECORDS:
                raise UnsafeWitness("witness log full")
            previous_record = _sha(records[-1]) if records else ZERO
            previous_checkpoint = (
                checkpoint_sha256(records[-1]["checkpoint"]) if records else ZERO)
            self._decode_checkpoint(
                checkpoint, generation=n,
                prior_checkpoint_sha256=previous_checkpoint)
            payload = {
                "schema": 1, "kind": "synthetic_observer_highwater",
                "generation": n,
                "previous_record_sha256": previous_record,
                "checkpoint": checkpoint,
                "observer_fingerprint": self.fingerprint,
            }
            record = {
                **payload,
                "signature": _encode(self.signer.sign(DOMAIN + _json_bytes(payload))),
            }
            line = _json_bytes(record) + b"\n"
            if len(line) > MAX_LINE_BYTES or (
                os.fstat(fd).st_size + len(line) > MAX_LOG_BYTES
            ):
                raise UnsafeWitness("witness record exceeds size cap")
            os.lseek(fd, 0, os.SEEK_END)
            if os.write(fd, line) != len(line):
                raise UnsafeWitness("short write: manual review required")
            os.fsync(fd)
            # Refuse to return the new anchor until the whole log was
            # re-read and signature/chain validated on the SAME inode.
            written = self._parse(fd)
            if len(written) != n or _sha(written[-1]) != _sha(record):
                raise UnsafeWitness("written checkpoint not verified")
            if genesis:
                root_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(root_fd)
                finally:
                    os.close(root_fd)
            return self._anchor(written)
        finally:
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit("Research-only cooperative witness, NO model dispatch")
