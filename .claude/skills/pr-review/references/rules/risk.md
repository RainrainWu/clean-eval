## Risk

| ID | Severity | Min tier | Rule |
|---|---|---|---|
| R-01 | major | High | **Blast radius.** PR touching a 🟠/🔴 module states impact and a rollback/feature-flag path in its description. |
| R-02 | major | High | **Scope.** A 🟠/🔴 PR that mixes refactors with behaviour changes is split. |
| R-03 | blocker | Low | **Security.** No secrets, injection vectors, auth bypasses, or unsafe deserialization. |
| R-04 | blocker | Low | **Sensitive data.** No secret, credential or personal data is logged, printed, or put into errors, metrics or traces. |
| R-05 | major | Low | **Size.** More than ~25 files or ~500 lines of meaningful change (generated files aside), or ~400 lines in a 🟠/🔴 PR, is split into PRs a reviewer can hold in mind. |
| R-06 | major | Low | **Least privilege.** Permissions, roles, scopes and tokens the change grants are the minimum it needs. |
