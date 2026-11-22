"""External layer: the outermost ring, and the only code that touches anything outside the process.

  the drivers    cli.py, the command line (built on click; every utility is one of its commands, the CI ones included)
                 and the composition root, which builds the gateways and presenters it hands the use cases;
                 agent_guard.py, the CI agent's hook (the `guard` command)
  the gateways   one class per external dependency, holding everything about it (its commands, formats and conventions)
                 and mapping its failures to the domain's errors. Each implements one of the use cases' interfaces
                 (use_cases/interfaces.py), and nothing more:
                   git.py              GitGateway(Git)                     the git CLI, its diffs and content ids
                   github.py           GitHubGateway(CodeHost)             GitHub, through the gh CLI
                   github_actions.py   GitHubActionsGateway(Runner)        the runner's step outputs and summary
                   claude_code.py      ClaudeCodeGateway(Agent)            the CI agent: the claude CLI, sandboxed
                   cobertura.py        CoberturaGateway(CoverageReport)    the coverage report (coverage.xml)
                   filesystem.py       FileSystemGateway(FileSystem)       files and folders
                   environment.py      EnvironmentGateway(Environment)     environment variables and their secrets
                                       ClockGateway(Clock)                 today's date

tests/test_architecture.py checks that each dependency is reached only through its gateway."""
