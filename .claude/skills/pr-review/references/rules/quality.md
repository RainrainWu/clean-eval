## Quality

| ID | Severity | Min tier | Rule |
|---|---|---|---|
| Q-01 | major | Moderate | **Tests follow behaviour.** A behaviour change or a new critical path ships with a test that fails without it, and the tests it affects are updated; the finding names what to test. In 🟠/🔴 modules this is a `blocker`. |
| Q-02 | major | Moderate | **Failure paths.** Errors are handled where they can be: new I/O handles timeouts, retries/idempotency and partial failure, and exceptions are caught narrowly and never swallowed; the finding suggests the handling. |
| Q-03 | major | Low | **Contracts.** Changes to public APIs, schemas, events, or persisted formats are backward compatible or include a migration/versioning plan. |
| Q-04 | minor | Moderate | **Clarity.** Names (of models and fields too) are self-explanatory and can't be mistaken for similar ones; dead code and complexity are flagged when they materially hurt the reader. |
| Q-05 | blocker | Low | **No stray globals.** A module-level value that is really configuration or input is a setting or a parameter, not a global. |
| Q-06 | major | Moderate | **Function size.** A function over ~50 lines (tests aside) is split at its steps; the finding says where. |
| Q-07 | major | Moderate | **Parameter count.** A function taking more than 7 parameters groups related ones into a model; the finding proposes which. |
| Q-08 | major | Moderate | **Reuse.** A new helper doesn't duplicate one the repository already has; the finding names the one to reuse. |
| Q-09 | major | Moderate | **Concise docs.** Docstrings and comments are short and say what the code can't: one line where one will do, never a restatement of the code. |
| Q-10 | major | Moderate | **Meaningful tests.** No test only confirms that nothing happens where no logic exists. |
| Q-11 | major | Low | **Dependencies.** No dependency is added, or left behind, unused. |
| Q-12 | minor | Low | **Docs follow code.** The README and agent instructions (AGENTS.md, CLAUDE.md, skills) reflect the change. |
