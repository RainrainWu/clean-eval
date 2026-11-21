"""External layer: the outermost ring, and the only code that touches anything outside the process.

  the drivers        cli.py, ci.py (the command line), agent_guard.py (the CI agent's hook), wiring.py (the
                     composition root: builds the gateways, the adaptors over them, and the use cases)
  the gateways       one class per external dependency, encapsulating every use of it (its commands, queries and
                     output formats) and mapping its failures to the domain's errors:
                       git.py                GitGateway              the git CLI (also the use cases' Repository port)
                       github.py             GitHubGateway           the GitHub CLI, gh (also the CodeHost port)
                       code_review_graph.py  CodeReviewGraphGateway  the code-review-graph CLI
                       sqlite.py             SqliteGateway           an SQLite database, read-only
                       filesystem.py         FileSystemGateway       files and folders
                       environment.py        EnvironmentGateway      environment variables, the home folder
                       clock.py              ClockGateway            today's date

The adaptors receive the gateways through the protocols in adaptor/ports.py. tests/test_architecture.py checks that
each dependency is reached only through its gateway."""
