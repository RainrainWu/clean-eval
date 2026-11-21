"""clean-eval, in Clean Architecture layers: domain <- use_cases <- adaptor <- external. Dependencies point inward only;
the two inner layers are pure, and the domain holds data models only. Run as `uv run python -I <checkout>/clean_eval
<command>` (see external/cli.py)."""
