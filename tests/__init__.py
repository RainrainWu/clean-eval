"""The test suite, laid out like the package: tests/<layer>/ holds the tests of clean_eval/<layer>/ (test_<module>.py
for <module>.py) and follows the same dependency rule; the tests at this level span the layers (the architecture rules,
the GitHub Action end to end, the release check). Fixtures live in the conftest.py at the level of the tests that use
them; the ones in this folder's serve every layer."""
