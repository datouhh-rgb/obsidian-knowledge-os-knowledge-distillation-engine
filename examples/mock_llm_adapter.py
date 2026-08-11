"""Protocol smoke adapter; deterministic and not an LLM.

Read one Analyzer request from stdin and emit a minimal evidence-grounded JSON
response. Use only to test command transport wiring.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    request = json.load(sys.stdin)
    evidence = request.get("evidence_selection_candidates", [])
    if not evidence:
        json.dump({"concepts": [], "relations": [], "skills": [], "warnings": ["No evidence supplied."]}, sys.stdout)
        return 0
    first = evidence[0]
    payload = {
        "concepts": [{
            "title": "协议测试候选",
            "definition": {
                "text": str(first.get("excerpt", "")),
                "selected_evidence_indexes": [first["index"]],
            },
            "confidence": 0.1,
        }],
        "relations": [],
        "skills": [],
        "warnings": ["Deterministic protocol smoke adapter; not a production LLM."],
    }
    json.dump(payload, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
