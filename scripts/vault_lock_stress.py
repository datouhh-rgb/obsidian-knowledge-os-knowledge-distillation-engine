#!/usr/bin/env python3
"""Run a bounded, diagnostic Vault-lock contention stress check."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import platform
from pathlib import Path
import sys
import tempfile
import time

from knowledge_engine.transaction import VaultTransaction


def run_stress(vault: Path, rounds: int = 20) -> dict[str, object]:
    vault.mkdir(parents=True, exist_ok=True)
    durations: list[float] = []
    failures: list[dict[str, object]] = []

    def commit(round_index: int, worker: int) -> dict[str, object]:
        started = time.monotonic()
        try:
            with VaultTransaction(
                vault,
                operation="lock-stress",
                idempotency_key=f"lock-stress-{round_index}-{worker}",
                request_payload={"round": round_index, "worker": worker},
                lock_timeout=10.0,
            ) as tx:
                tx.stage_text(
                    Path(".knowledge-engine/lock-stress") / f"{round_index:02d}-{worker}.txt",
                    f"round={round_index};worker={worker}\n",
                )
                result = tx.commit({"round": round_index, "worker": worker})
            return {"passed": True, "revision": result.revision, "seconds": time.monotonic() - started}
        except Exception as exc:  # pragma: no cover - recorded as machine evidence
            return {
                "passed": False,
                "seconds": time.monotonic() - started,
                "error": f"{type(exc).__name__}: {exc}",
            }

    for round_index in range(1, rounds + 1):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda worker: commit(round_index, worker), (1, 2)))
        for worker, result in enumerate(results, start=1):
            durations.append(float(result["seconds"]))
            if not result["passed"]:
                failures.append({"round": round_index, "worker": worker, **result})

    state_path = vault / ".knowledge-engine/STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    retained_journals = list((vault / ".knowledge-engine/journal").glob("*.json"))
    incomplete_journals = [
        path for path in retained_journals
        if json.loads(path.read_text(encoding="utf-8")).get("status") not in {"committed", "rolled_back"}
    ]
    leaked_staging = list((vault / ".knowledge-engine/staging").glob("*"))
    expected_revision = rounds * 2
    passed = (
        not failures
        and int(state.get("revision", -1)) == expected_revision
        and not incomplete_journals
        and not leaked_staging
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "os": platform.platform(),
        "python": sys.version,
        "rounds": rounds,
        "contending_workers_per_round": 2,
        "transactions": rounds * 2,
        "expected_revision": expected_revision,
        "observed_revision": state.get("revision"),
        "max_lock_transaction_seconds": round(max(durations, default=0.0), 6),
        "average_lock_transaction_seconds": round(sum(durations) / max(len(durations), 1), 6),
        "failure_count": len(failures),
        "failures": failures,
        "retained_audit_journal_count": len(retained_journals),
        "incomplete_journals": [str(item) for item in incomplete_journals],
        "leaked_staging": [str(item) for item in leaked_staging],
        "status": "passed" if passed else "blocked",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.rounds < 20:
        raise SystemExit("The production contention diagnostic requires at least 20 rounds.")
    with tempfile.TemporaryDirectory(prefix="v33-vault-lock-stress-") as temporary:
        result = run_stress(Path(temporary) / "vault", args.rounds)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
