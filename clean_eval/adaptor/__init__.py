"""Adaptor layer: translation between the use cases and the outside world, which it never touches itself. The sources,
stores, graph builders and coverage reader implement the use cases' ports over the external layer's gateways (given as
the protocols in ports.py); the code analysis (ast) and the presenters (SVG, Markdown) are pure."""
