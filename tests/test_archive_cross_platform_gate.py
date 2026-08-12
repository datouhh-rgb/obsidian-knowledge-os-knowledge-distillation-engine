from __future__ import annotations

import json
from pathlib import Path

from scripts.aggregate_v33_archive_verification import aggregate


def test_final_archive_requires_same_bytes_on_three_operating_systems(tmp_path: Path) -> None:
    commit = "a" * 40
    workflow = "https://github.example/actions/runs/3"
    paths = []
    for system in ("Windows", "Linux", "Darwin"):
        path = tmp_path / system / "archive-verification.json"
        path.parent.mkdir()
        path.write_text(json.dumps({
            "os": system, "commit_sha": commit, "workflow_url": workflow,
            "archive_sha256": "b" * 64, "status": "passed",
        }), encoding="utf-8")
        paths.append(path)
    assert aggregate(paths, commit, workflow)["status"] == "passed"
    changed = json.loads(paths[0].read_text(encoding="utf-8"))
    changed["archive_sha256"] = "c" * 64
    paths[0].write_text(json.dumps(changed), encoding="utf-8")
    assert aggregate(paths, commit, workflow)["status"] == "blocked"
