# V3.1.1 LTS Reviewer Brief

Review these files first:

1. `FINAL_LTS_REPORT.md`
2. `FINAL_LTS_AUDIT/CHECK_RESULTS.json`
3. `FINAL_LTS_AUDIT/MANIFEST.sha256`
4. `.github/workflows/v3.1-cross-platform.yml`
5. `CHANGELOG.md`

The LTS decision is fail-closed. It requires named passing checks for manifest integrity, compilation, full pytest, the exact six-job operating-system/runtime matrix, Evidence validation, Skill validation, migration, exposure create/discover/remove/recreate, and rollback. Test counts alone cannot approve the release.

Rerun with the consolidated CI evidence downloaded from the official workflow:

```text
python scripts/final_lts_check.py --cross-platform-evidence <cross-platform-evidence.json>
```

Accept only the exact output:

```text
SUCCESS:
V3.1_LTS_READY
```

`V3.1_LTS_BLOCKED` means the package remains an audited stability candidate, not an LTS-ready release.
