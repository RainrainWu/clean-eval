"""The test suite, laid out like the package: tests/<layer>/ holds the tests of clean_eval/<layer>/ (test_<module>.py for
<module>.py) and follows the same dependency rule; the tests at this level span the layers (the architecture rules,
action.sh end to end, the release check).

Every git call in the tests, and in the code under test, ignores the developer's git config: it is set here, before any
test module is imported."""
import os
import tempfile

_GITCONFIG = os.path.join(tempfile.mkdtemp(prefix="clean-eval-gitconfig-"), "gitconfig")
with open(_GITCONFIG, "w") as f:
    f.write("[user]\n\tname = Test\n\temail = test@example.com\n[commit]\n\tgpgsign = false\n"
            "[tag]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n[protocol \"file\"]\n\tallow = always\n")
os.environ["GIT_CONFIG_GLOBAL"] = _GITCONFIG
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
