# Plans

Future work items tracked here.

---

## Update Cargo lockfile parser to track checksums for git and path packages

Cargo.lock doesn't innately provide checksums for packages that are provided from git repos or from paths on disk. We want to skip workspace members (since those files are part of the current git repo), but fetch git commit shas from git repositories or paths on disk. We also want to fail if there is a dependency with a path on disk but the git repository at that path is dirty, since that would mean inputs that aren't tracked by the git sha.

After we track checksums for git repository dependencies and path dependencies outside the project's git repository, we should error on any cargo dependencies that don't have a checksum or git commit sha.
