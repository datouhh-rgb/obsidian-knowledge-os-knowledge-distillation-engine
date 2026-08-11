from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from .utils import (
    _fsync_directory,
    atomic_write,
    canonical_json,
    now_iso,
    safe_relative_target,
    sha256_bytes,
    sha256_file,
    sha256_text,
)


STATE_PATH = Path(".knowledge-engine/STATE.json")
JOURNAL_ROOT = Path(".knowledge-engine/journal")
STAGING_ROOT = Path(".knowledge-engine/staging")
LOCK_PATH = Path(".knowledge-engine/vault.lock")


class TransactionError(RuntimeError):
    pass


class RevisionConflict(TransactionError):
    pass


class IdempotencyConflict(TransactionError):
    pass


class ManualRecoveryRequired(TransactionError):
    pass


def remove_exposure_link(path: Path) -> None:
    """Remove an exposure path without traversing a symlink or junction target.

    Production callers must still verify ownership and the expected target before
    calling this helper.  The directory branch exists for explicit fixture and
    operator cleanup; normal exposure removal refuses entity directories before
    reaching this function.
    """
    path = Path(path)
    if not os.path.lexists(str(path)):
        return
    if path.is_symlink():
        path.unlink()
        _fsync_directory(path.parent)
        return
    if os.name == "nt":
        try:
            attributes = path.stat(follow_symlinks=False).st_file_attributes  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            attributes = 0
        if attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT: junction/reparse link.
            path.rmdir()
            _fsync_directory(path.parent)
            return
    if path.is_dir():
        shutil.rmtree(path)
        _fsync_directory(path.parent)
        return
    raise TransactionError(f"Exposure path is neither a directory link nor a directory: {path}")


class SimulatedCrash(BaseException):
    """Fault-injection signal that intentionally leaves a WAL for startup recovery."""


@dataclass(frozen=True)
class CommitResult:
    transaction_id: str
    revision: int
    written_paths: tuple[Path, ...]
    duplicate: bool
    event_id: str | None


class VaultTransaction:
    """Vault-wide lock plus hash-reconciled write-ahead log.

    Recovery never trusts a progress counter. Every operation records its before and
    after state, and startup recovery compares those records with the actual target.
    """

    def __init__(
        self,
        vault: Path,
        operation: str,
        idempotency_key: str | None = None,
        request_payload: dict[str, Any] | None = None,
        expected_revision: int | None = None,
        lock_timeout: float = 30.0,
        fail_after: int | None = None,
        crash_after: int | None = None,
        crash_after_replace: int | None = None,
    ) -> None:
        self.vault = vault.resolve()
        self.operation = operation
        self.idempotency_key = idempotency_key
        self.request_payload = request_payload or {}
        self.request_payload_hash = sha256_text(canonical_json(self.request_payload))
        self.expected_revision = expected_revision
        self.lock = FileLock(str(self.vault / LOCK_PATH), timeout=lock_timeout)
        self.transaction_id = "tx-" + str(uuid.uuid4())
        self.event_id = "event-" + str(uuid.uuid4())
        self.fail_after = fail_after
        self.crash_after = crash_after
        self.crash_after_replace = crash_after_replace
        self.state: dict[str, Any] = {}
        self.operations: list[dict[str, Any]] = []
        self.duplicate = False
        self.prior_event: dict[str, Any] | None = None
        self._entered = False
        self._committed = False

    def __enter__(self) -> "VaultTransaction":
        if not self.vault.is_dir():
            raise TransactionError(f"Vault does not exist: {self.vault}")
        (self.vault / ".knowledge-engine").mkdir(parents=True, exist_ok=True)
        try:
            self.lock.acquire()
        except Timeout as exc:
            raise TransactionError("Timed out acquiring the Vault process lock.") from exc
        self._entered = True
        self.recover_locked(self.vault)
        self.state = self._load_state()
        revision = int(self.state.get("revision", 0))
        if self.expected_revision is not None and self.expected_revision != revision:
            self.lock.release()
            self._entered = False
            raise RevisionConflict(f"CAS revision conflict: expected={self.expected_revision}, actual={revision}")
        if self.idempotency_key:
            prior = self.state.setdefault("idempotency", {}).get(self.idempotency_key)
            if isinstance(prior, dict):
                same_operation = prior.get("operation") == self.operation
                same_payload = prior.get("request_payload_hash") == self.request_payload_hash
                if not (same_operation and same_payload):
                    self.lock.release()
                    self._entered = False
                    raise IdempotencyConflict(
                        "Idempotency key already belongs to a different operation or request payload."
                    )
                self.duplicate = True
                self.prior_event = prior
        return self

    def stage_text(self, relative: Path | str, content: str) -> None:
        self.stage_bytes(relative, content.encode("utf-8"))

    def stage_bytes(self, relative: Path | str, content: bytes) -> None:
        if self.duplicate:
            return
        target = safe_relative_target(self.vault, relative)
        self.operations.append({
            "type": "write",
            "relative": Path(relative).as_posix(),
            "target": str(target),
            "content_b64": base64.b64encode(content).decode("ascii"),
            "after_sha256": sha256_bytes(content),
        })

    def stage_delete(self, relative: Path | str) -> None:
        if self.duplicate:
            return
        target = safe_relative_target(self.vault, relative)
        self.operations.append({
            "type": "delete", "relative": Path(relative).as_posix(),
            "target": str(target), "after_sha256": None,
        })

    def stage_link_create(self, link_path: Path, target_path: Path) -> None:
        if self.duplicate:
            return
        self.operations.append({
            "type": "link-create", "target": str(link_path.absolute()),
            "link_target": str(target_path.resolve()),
        })

    def stage_link_remove(self, link_path: Path) -> None:
        if self.duplicate:
            return
        self.operations.append({"type": "link-remove", "target": str(link_path.absolute())})

    def commit(self, event_result: dict[str, Any] | None = None) -> CommitResult:
        if not self._entered:
            raise TransactionError("Transaction has not been entered.")
        if self._committed:
            raise TransactionError("Transaction has already been committed.")
        if self.duplicate:
            self._committed = True
            return CommitResult(
                self.transaction_id, int(self.state.get("revision", 0)), (), True,
                None if self.prior_event is None else str(self.prior_event.get("event_id")),
            )

        revision_before = int(self.state.get("revision", 0))
        revision_after = revision_before + 1
        new_state = json.loads(json.dumps(self.state))
        new_state.update({"schema_version": 4, "revision": revision_after, "updated_at": now_iso()})
        ledger_operations = [
            item for item in self.operations
            if item.get("type") == "write" and item.get("relative") == "05_Knowledge/Audit/ASSET_LEDGER.json"
        ]
        if ledger_operations:
            if len(ledger_operations) != 1:
                raise TransactionError("A transaction may stage the upper asset ledger only once.")
            new_state["asset_ledger_sha256"] = ledger_operations[0]["after_sha256"]
            new_state["asset_ledger_revision"] = revision_after
        if self.idempotency_key:
            new_state.setdefault("idempotency", {})[self.idempotency_key] = {
                "key": self.idempotency_key,
                "event_id": self.event_id,
                "operation": self.operation,
                "request_payload_hash": self.request_payload_hash,
                "transaction_id": self.transaction_id,
                "revision": revision_after,
                "result": event_result or {},
                "created_at": now_iso(),
            }
        self.stage_text(STATE_PATH, json.dumps(new_state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

        stage_dir = self.vault / STAGING_ROOT / self.transaction_id
        stage_dir.mkdir(parents=True, exist_ok=False)
        _fsync_directory(stage_dir.parent)
        prepared = [self._prepare_operation(item, index, stage_dir) for index, item in enumerate(self.operations)]
        journal_path = self.vault / JOURNAL_ROOT / f"{self.transaction_id}.json"
        journal = {
            "schema_version": 4,
            "transaction_id": self.transaction_id,
            "event_id": self.event_id,
            "operation": self.operation,
            "idempotency_key": self.idempotency_key,
            "request_payload_hash": self.request_payload_hash,
            "status": "prepared",
            "revision_before": revision_before,
            "revision_after": revision_after,
            "created_at": now_iso(),
            # Retained for V3 readers only; recovery deliberately ignores it.
            "applied": 0,
            "operations": prepared,
        }
        self._write_journal(journal_path, journal)
        written: list[Path] = []
        try:
            journal["status"] = "applying"
            self._write_journal(journal_path, journal)
            for index, operation in enumerate(prepared):
                if self.fail_after is not None and index >= self.fail_after:
                    raise OSError("fault-injection: transaction write")
                operation["operation_state"] = "replace_pending"
                self._write_journal(journal_path, journal)
                target = Path(operation["target"])
                changed = self._apply_operation(operation)
                if changed:
                    written.append(target)
                if self.crash_after_replace is not None and index + 1 >= self.crash_after_replace:
                    # The target now contains after-bytes while the durable marker is stale.
                    raise SimulatedCrash("fault-injection: crash after replace before applied marker")
                operation["operation_state"] = "applied"
                operation["applied_at"] = now_iso()
                journal["applied"] = index + 1
                self._write_journal(journal_path, journal)
                if self.crash_after is not None and index + 1 >= self.crash_after:
                    raise SimulatedCrash("fault-injection: simulated process crash")
            journal["status"] = "committed"
            journal["committed_at"] = now_iso()
            self._write_journal(journal_path, journal)
            self._cleanup_stage(stage_dir)
            self.state = new_state
            self._committed = True
            return CommitResult(self.transaction_id, revision_after, tuple(written), False, self.event_id)
        except SimulatedCrash:
            raise
        except Exception:
            self._rollback_journal(journal, journal_path)
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
        try:
            if exc_type is not None and exc_type is not SimulatedCrash and not self._committed:
                journal_path = self.vault / JOURNAL_ROOT / f"{self.transaction_id}.json"
                if journal_path.is_file():
                    journal = json.loads(journal_path.read_text(encoding="utf-8"))
                    self._rollback_journal(journal, journal_path)
        finally:
            if self._entered:
                self.lock.release()
                self._entered = False

    def _prepare_operation(self, operation: dict[str, Any], index: int, stage_dir: Path) -> dict[str, Any]:
        target = Path(operation["target"])
        if operation["type"] in {"write", "delete"}:
            if self._path_lexists(target) and not target.is_file():
                raise TransactionError(f"File transaction target is not a regular file: {target}")
            before = target.read_bytes() if target.is_file() else None
            prepared = {
                **operation,
                "before_exists": before is not None,
                "before_b64": None if before is None else base64.b64encode(before).decode("ascii"),
                "before_sha256": None if before is None else sha256_bytes(before),
                "operation_state": "prepared",
            }
            if operation["type"] == "write":
                staged = stage_dir / f"{index:04d}.new"
                atomic_write(staged, base64.b64decode(operation["content_b64"]))
                prepared["staged"] = str(staged)
                prepared.pop("content_b64", None)
            return prepared

        before = self._link_state(target)
        if before["kind"] == "entity":
            raise TransactionError(f"Exposure target is an entity and will not be modified: {target}")
        after = (
            {"kind": "link", "target": str(Path(operation["link_target"]).resolve())}
            if operation["type"] == "link-create" else {"kind": "absent"}
        )
        return {
            **operation,
            "before_link_state": before,
            "after_link_state": after,
            "operation_state": "prepared",
        }

    @classmethod
    def _apply_operation(cls, operation: dict[str, Any]) -> bool:
        target = Path(operation["target"])
        if operation["type"] == "write":
            content = Path(operation["staged"]).read_bytes()
            if target.is_file() and sha256_file(target) == operation["after_sha256"]:
                return False
            atomic_write(target, content)
            return True
        if operation["type"] == "delete":
            if not cls._path_lexists(target):
                return False
            if not target.is_file():
                raise TransactionError(f"Refusing to delete non-file target: {target}")
            target.unlink()
            _fsync_directory(target.parent)
            return True
        if operation["type"] == "link-create":
            actual = cls._link_state(target)
            desired = operation["after_link_state"]
            if actual == desired:
                return False
            if actual["kind"] != "absent":
                raise TransactionError(f"Exposure link conflict: {target}")
            cls._create_directory_link(target, Path(operation["link_target"]))
            return True
        if operation["type"] == "link-remove":
            actual = cls._link_state(target)
            if actual["kind"] == "absent":
                return False
            if actual["kind"] != "link":
                raise TransactionError(f"Refusing to remove non-link exposure: {target}")
            cls._remove_directory_link(target)
            return True
        raise TransactionError(f"Unknown transaction operation: {operation['type']}")

    def _load_state(self) -> dict[str, Any]:
        path = self.vault / STATE_PATH
        if not path.is_file():
            return {"schema_version": 4, "revision": 0, "idempotency": {}, "updated_at": None}
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TransactionError(f"Vault STATE cannot be read: {exc}") from exc
        if not isinstance(state, dict) or not isinstance(state.get("revision"), int) or not isinstance(state.get("idempotency", {}), dict):
            raise TransactionError("Vault STATE structure is invalid.")
        return state

    @classmethod
    def recover(cls, vault: Path, lock_timeout: float = 30.0) -> list[str]:
        resolved = vault.resolve()
        (resolved / ".knowledge-engine").mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(resolved / LOCK_PATH), timeout=lock_timeout)
        try:
            with lock:
                return cls.recover_locked(resolved)
        except Timeout as exc:
            raise TransactionError("Timed out acquiring the Vault recovery lock.") from exc

    @classmethod
    def recover_locked(cls, vault: Path) -> list[str]:
        recovered: list[str] = []
        root = vault / JOURNAL_ROOT
        if not root.is_dir():
            return recovered
        for journal_path in sorted(root.glob("*.json")):
            try:
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise TransactionError(f"WAL is corrupt and cannot be deterministically recovered: {journal_path}: {exc}") from exc
            if journal.get("status") in {"committed", "rolled_back"}:
                continue
            cls._rollback_journal(journal, journal_path)
            recovered.append(str(journal.get("transaction_id", journal_path.stem)))
        return recovered

    @classmethod
    def _rollback_journal(cls, journal: dict[str, Any], journal_path: Path) -> None:
        operations = journal.get("operations", [])
        if not isinstance(operations, list):
            raise ManualRecoveryRequired(f"WAL operations are invalid: {journal_path}")
        try:
            for operation in reversed(operations):
                cls._rollback_operation(operation)
            for operation in operations:
                if not cls._matches_before(operation):
                    raise ManualRecoveryRequired(f"Rollback verification failed for {operation.get('target')}")
        except ManualRecoveryRequired as exc:
            journal["status"] = "manual_recovery_required"
            journal["recovery_error"] = str(exc)
            journal["recovery_checked_at"] = now_iso()
            cls._write_journal(journal_path, journal)
            raise
        journal["status"] = "rolled_back"
        journal["rolled_back_at"] = now_iso()
        cls._write_journal(journal_path, journal)
        tx_id = str(journal.get("transaction_id", journal_path.stem))
        cls._cleanup_stage(journal_path.parents[1] / "staging" / tx_id)

    @classmethod
    def _rollback_operation(cls, operation: dict[str, Any]) -> None:
        target = Path(operation["target"])
        operation_type = operation.get("type")
        if operation_type in {"write", "delete"}:
            actual = cls._file_state(target)
            before = {
                "kind": "file", "sha256": operation.get("before_sha256")
            } if operation.get("before_exists") else {"kind": "absent"}
            after = {
                "kind": "file", "sha256": operation.get("after_sha256")
            } if operation_type == "write" else {"kind": "absent"}
            if actual == before:
                operation["operation_state"] = "before_verified"
                return
            if actual != after:
                raise ManualRecoveryRequired(
                    f"Target matches neither before nor after state: {target}; actual={actual}"
                )
            if before["kind"] == "file":
                payload = operation.get("before_b64")
                if not isinstance(payload, str):
                    raise ManualRecoveryRequired(f"WAL lacks before content for {target}")
                atomic_write(target, base64.b64decode(payload))
            elif cls._path_lexists(target):
                if not target.is_file():
                    raise ManualRecoveryRequired(f"Refusing to delete unexpected entity: {target}")
                target.unlink()
                _fsync_directory(target.parent)
            operation["operation_state"] = "rolled_back"
            return

        actual_link = cls._link_state(target)
        before_link = operation.get("before_link_state")
        after_link = operation.get("after_link_state")
        # Compatibility with V3 journals.
        if not isinstance(before_link, dict):
            before_link = (
                {"kind": "link", "target": str(Path(operation["before_target"]).resolve())}
                if operation.get("before_exists") and operation.get("before_is_link")
                else {"kind": "absent"}
            )
        if not isinstance(after_link, dict):
            after_link = (
                {"kind": "link", "target": str(Path(operation["link_target"]).resolve())}
                if operation_type == "link-create" else {"kind": "absent"}
            )
        if actual_link == before_link:
            operation["operation_state"] = "before_verified"
            return
        if actual_link != after_link:
            raise ManualRecoveryRequired(
                f"Exposure target matches neither before nor after state: {target}; actual={actual_link}"
            )
        if actual_link["kind"] == "link":
            cls._remove_directory_link(target)
        if before_link["kind"] == "link":
            cls._create_directory_link(target, Path(before_link["target"]))
        operation["operation_state"] = "rolled_back"

    @classmethod
    def _matches_before(cls, operation: dict[str, Any]) -> bool:
        target = Path(operation["target"])
        if operation.get("type") in {"write", "delete"}:
            expected = (
                {"kind": "file", "sha256": operation.get("before_sha256")}
                if operation.get("before_exists") else {"kind": "absent"}
            )
            return cls._file_state(target) == expected
        expected_link = operation.get("before_link_state")
        if not isinstance(expected_link, dict):
            expected_link = (
                {"kind": "link", "target": str(Path(operation["before_target"]).resolve())}
                if operation.get("before_exists") and operation.get("before_is_link")
                else {"kind": "absent"}
            )
        return cls._link_state(target) == expected_link

    @staticmethod
    def _file_state(path: Path) -> dict[str, Any]:
        if not os.path.lexists(str(path)):
            return {"kind": "absent"}
        if path.is_file() and not path.is_symlink():
            return {"kind": "file", "sha256": sha256_file(path)}
        return {"kind": "entity"}

    @classmethod
    def _link_state(cls, path: Path) -> dict[str, Any]:
        if not cls._path_lexists(path):
            return {"kind": "absent"}
        if cls._is_link_like(path):
            try:
                resolved = str(path.resolve(strict=True))
            except OSError:
                # Broken symlinks remain links; normalize their declared target.
                if path.is_symlink():
                    declared = Path(os.readlink(path))
                    resolved = str((path.parent / declared).resolve()) if not declared.is_absolute() else str(declared.resolve())
                else:
                    return {"kind": "link", "target": None}
            return {"kind": "link", "target": resolved}
        return {"kind": "entity"}

    @staticmethod
    def _write_journal(path: Path, journal: dict[str, Any]) -> None:
        atomic_write(path, json.dumps(journal, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def _cleanup_stage(stage_dir: Path) -> None:
        if stage_dir.is_dir():
            shutil.rmtree(stage_dir)
            _fsync_directory(stage_dir.parent)

    @staticmethod
    def _is_link_like(path: Path) -> bool:
        if path.is_symlink():
            return True
        try:
            attributes = path.stat(follow_symlinks=False).st_file_attributes  # type: ignore[attr-defined]
            return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
        except (AttributeError, OSError):
            return False

    @staticmethod
    def _path_lexists(path: Path) -> bool:
        return os.path.lexists(str(path))

    @staticmethod
    def _create_directory_link(link: Path, target: Path) -> None:
        if not target.is_dir():
            raise TransactionError(f"Exposure target directory does not exist: {target}")
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(target, link, target_is_directory=True)
            _fsync_directory(link.parent)
            return
        except OSError as exc:
            if os.name != "nt":
                raise TransactionError(f"Cannot create POSIX directory symlink: {exc}") from exc
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, shell=False,
        )
        if completed.returncode != 0 or not cls_path_lexists(link):
            raise TransactionError(f"Cannot create Windows directory junction: {completed.stderr.strip()}")

    @staticmethod
    def _remove_directory_link(link: Path) -> None:
        if not VaultTransaction._is_link_like(link):
            raise TransactionError(f"Refusing to remove non-link path: {link}")
        if not link.is_symlink() and os.name != "nt":
            raise TransactionError(f"Non-symlink reparse-like path is unsupported on POSIX: {link}")
        remove_exposure_link(link)


def cls_path_lexists(path: Path) -> bool:
    """Small top-level helper used after Windows mklink returns."""
    return os.path.lexists(str(path))
