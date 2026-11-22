"""Use-case layer: the business logic. Pure (no I/O), and in business terms: no external tool's formats or conventions.

interfaces.py   the abstract classes of every external utility the business logic uses, which the outer layers
                implement: the gateways (Git, CodeHost, FileSystem, CoverageReport, Agent, Environment, Clock, Runner),
                the presenters (DiagramPresenter, ReportPresenter) and the console (RunLog)
the rest        module-level functions that take implementations of those interfaces and call only their abstract
                methods: one module per feature (heatmap.py, review.py, retro.py, each with its skill's CI run),
                what they share (store.py, a run's files; ci.py, the steps of every CI run), and rules/, the business
                rules: pure functions over the domain models
"""
