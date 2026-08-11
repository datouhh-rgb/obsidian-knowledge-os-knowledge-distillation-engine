# V3.1.1 LTS cross-platform support

The official workflow runs six required jobs:

| Operating system | Python 3.11 | Python 3.12 |
|---|---|---|
| Windows | Required | Required |
| Ubuntu Linux | Required | Required |
| macOS | Required | Required |

Every job runs the full pytest suite plus dedicated exposure, migration, symlink, and path-handling checks. Raw logs are hashed and consolidated for `scripts/final_lts_check.py`. Any missing or failed job produces `V3.1_LTS_BLOCKED`.

Cleanup behavior:

- POSIX and Windows directory symlinks use `unlink()`; the target is never traversed.
- A validated Windows junction uses the platform's junction-directory removal operation.
- An explicitly supplied ordinary cleanup directory uses `shutil.rmtree()`.
- Production `unexpose` still refuses a regular file or directory that has replaced a managed link.

The create/discover/remove/recreate regression runs with a Unicode path containing spaces on all three operating systems.
