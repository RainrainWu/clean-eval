"""External layer: the outermost ring, and the only code that touches anything outside the process.

  the drivers        cli.py (the command line, built on click: every utility is one of its commands, the CI ones
                     included; also the composition root that builds the gateways and presenters it hands to the
                     use cases), agent_guard.py (the CI agent's hook, run as the `guard` command)
  the gateways       one class per external dependency, encapsulating every use of it (its commands, queries and
                     output formats) and mapping its failures to the domain's errors. Each implements one of the use
                     cases' interfaces (use_cases/interfaces.py), and nothing more:
                       git.py                GitGateway(Git)                         the git CLI
                       github.py             GitHubGateway(CodeHost)                 the GitHub CLI, gh
                       claude_code.py        ClaudeCodeGateway(Agent)                the CI agent: the claude CLI, run
                                                                                     sandboxed, its npm install, and
                                                                                     the job's OIDC token for keyless
                                                                                     sign-in
                       filesystem.py         FileSystemGateway(FileSystem)           files and folders
                       environment.py        EnvironmentGateway(Environment)         environment variables, the home folder
                                             ClockGateway(Clock)                     today's date

tests/test_architecture.py checks that each dependency is reached only through its gateway."""
