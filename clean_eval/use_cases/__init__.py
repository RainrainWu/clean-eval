"""Use-case layer: two things, both pure (imports only the domain layer and does no I/O).

  interfaces.py   the abstract classes of every external utility the business logic uses (Git, CodeHost, FileSystem,
                  KnowledgeGraph, Environment, Clock, and the DiagramPresenter and ReportPresenter), inherited and
                  implemented by the outer layers: the external layer's gateways and the adaptor layer's presenters
  every module    module-level functions: the use cases (rate a repository, assess a change, refresh the README, collect
  else            history; for CI: prepare the agent's context, finalize its output, post the review) and what they share
                  (sources, run files, tuning files, coverage), taking implementations of the interfaces as parameters
                  and calling only their abstract methods, plus rules/: the business rules, pure functions over the
                  domain models
"""
