# Controlled automation

Three active workflow files are provided:

- ci.yml: Windows/Linux Ruff, mypy, unit tests, synthetic smoke runs and package build.
- database.yml: a disposable PostGIS Docker service on the GitHub runner, Alembic
  migration and real integration tests. Its password is randomly generated at
  runtime and masked, never stored in the repository. The container is removed
  even after a failed test. It never accesses your Windows database.
- manual-run.yml: workflow_dispatch with an allowlisted synthetic dataset choice.
  It uses the file sink, no persistent database and no database secrets. Inputs
  pass through environment variables and a shell case allowlist, not executable
  expression interpolation. Only report.json, quality.json and issues.csv upload;
  raw sources, accepted features and quarantine records are excluded. Retention
  is seven days. Job timeouts bound execution.

All workflows use contents:read. No publishing, deployment, cloud provisioning or
recurring execution occurs. CI needs repository Actions to be enabled but has not
been pushed or executed remotely by this implementation.

## Weekly scheduling — disabled pending explicit approval

There is no active schedule key in any workflow. If the owner explicitly approves
weekly execution, add a separate scheduled file-only job using the same static
synthetic dataset allowlist and artifact restrictions. Example trigger only:

```yaml
# Not active configuration; do not copy until approved.
# on:
#   schedule:
#     - cron: '0 14 * * 1'  # Monday 14:00 UTC; DST changes local wall-clock time
```

Do not reuse inputs.dataset without a scheduled-event default. Do not assume
localhost on a hosted runner refers to the user's machine. Persistent database
runs require an explicitly approved reachable database and GitHub Actions secrets
for its URL; never put the connection string in workflow YAML. Sensitive dataset
reports require a separate disclosure review before artifact upload.

Dependency resolution is frozen by uv.lock, with uv0.12.12. Action major tags and
container version tags are not immutable digests; pin reviewed commit/image
digests before treating infrastructure bytes as fully reproducible.
