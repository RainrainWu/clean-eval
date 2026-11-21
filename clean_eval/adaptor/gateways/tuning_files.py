"""The repository's tuning files (`.clean-eval/` in the working tree) and the snapshot of them taken before the CI agent
ran: what an eval may change, and how anything it may not keep is put back."""
import filecmp
import os
import shutil

from ...domain.policy import REPO_DIR
from .files import read_text


def _text(path):
    return read_text(path) if os.path.isfile(path) else None


def _clear(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.remove(path)


class TuningFiles:
    def __init__(self, workspace, snapshot_dir):
        self.live, self.snap = os.path.join(workspace, REPO_DIR), snapshot_dir

    def snapshot(self):
        if os.path.isdir(self.live):
            shutil.copytree(self.live, self.snap, symlinks=True)

    def current(self, name):
        return _text(os.path.join(self.live, name))

    def original(self, name):
        return _text(os.path.join(self.snap, name))

    def restore(self, name):
        live, orig = os.path.join(self.live, name), os.path.join(self.snap, name)
        if os.path.isfile(orig):
            if not os.path.isfile(live) or not filecmp.cmp(live, orig, shallow=False):
                _clear(live)
                os.makedirs(os.path.dirname(live), exist_ok=True)
                shutil.copyfile(orig, live)
        else:
            _clear(live)

    def restore_others(self, keep):
        restored = set()
        for top, in_snapshot in ((self.live, False), (self.snap, True)):
            for root, _, names in os.walk(top) if os.path.isdir(top) else ():
                for name in names:
                    rel = os.path.relpath(os.path.join(root, name), top)
                    if rel in keep:
                        continue
                    live, orig = os.path.join(self.live, rel), os.path.join(self.snap, rel)
                    if not in_snapshot and not os.path.exists(orig):
                        os.remove(live)                                   # created by the agent
                    elif not os.path.exists(live) or not filecmp.cmp(live, orig, shallow=False):
                        os.makedirs(os.path.dirname(live), exist_ok=True)
                        shutil.copyfile(orig, live)                       # changed or removed by the agent
                    else:
                        continue
                    restored.add(rel)
        return sorted(restored)
