"""Use-case layer: two things, both pure (imports only the domain layer and does no I/O).

interfaces.py   the abstract classes of every external utility the business logic uses (Git, CodeHost, FileSystem,
                Agent, Environment, Clock, the DiagramPresenter and ReportPresenter, and RunLog),
                inherited and implemented by the outer layers: the external layer's gateways and command line, and
                the adaptor layer's presenters
every module    module-level functions that take implementations of the interfaces as parameters and call only their
else            abstract methods: one module per feature (heatmap.py: rate, level, summarize and publish the heat map;
                review.py: a change's risk, the CI reviewer's context, finalizing and posting the review;
                evaluation.py: the merged-PR history, the eval's context and tuning files, finalizing the eval; each
                also runs its skill in CI), store.py, the files of a run they share, and ci.py, the steps every CI
                run shares; plus rules/: the business rules, pure functions over the
                domain models (heatmap.py, review.py, evaluation.py, and analysis.py for reading code)
"""
