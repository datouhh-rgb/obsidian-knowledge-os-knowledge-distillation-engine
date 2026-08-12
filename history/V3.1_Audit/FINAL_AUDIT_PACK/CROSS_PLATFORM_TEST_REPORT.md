# Cross-platform Test Report

Result: `BLOCKED`

The Windows full suite is executed locally. Linux is accepted only from a local Linux run or a verified CI evidence JSON.
The CI definition is `.github/workflows/v3.1-cross-platform.yml`; macOS is documented as POSIX-compatible but is not a V3.1 accepted production platform.

## Commands and assertions

### Blocker 1: FAILED

Command: `C:\Users\ThinkPad\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q + verified Linux CI evidence`

Exit code: `1`

- PASS — windows_full_suite: `0`
- FAIL — linux_full_and_exposure_suite: `missing`
- FAIL — no_exposure_skip: `tests/test_exposure.py included in full suite`

Evidence:

- `FINAL_AUDIT_PACK/blockers/01/windows-full-suite.stdout.txt` — `4fc1ccdea9a9f925fe67e902dac0bf07982ec3f11637d223fa46f5cdefb4f9f2`
- `FINAL_AUDIT_PACK/blockers/01/windows-full-suite.stderr.txt` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
