"""External layer: the outermost ring, and the only code that touches anything outside the process.

  the drivers        cli.py (the command line, the CI commands included; also the composition root that builds the
                     gateways and presenters it hands to the use cases), agent_guard.py (the CI agent's hook)
  the gateways       one class per external dependency, encapsulating every use of it (its commands, queries and
                     output formats) and mapping its failures to the domain's errors. Each implements one of the use
                     cases' interfaces (use_cases/interfaces.py), and nothing more:
                       git.py                GitGateway(Git)                         the git CLI
                       github.py             GitHubGateway(CodeHost)                 the GitHub CLI, gh
                       code_review_graph.py  CodeReviewGraphGateway(KnowledgeGraph)  the code-review-graph CLI and the
                                                                                     SQLite database it builds
                       filesystem.py         FileSystemGateway(FileSystem)           files and folders
                       environment.py        EnvironmentGateway(Environment)         environment variables, the home folder
                                             ClockGateway(Clock)                     today's date

tests/test_architecture.py checks that each dependency is reached only through its gateway."""
