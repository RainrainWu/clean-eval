"""The repository's tuning files (`.clean-eval/` in the working tree) and the snapshot of them taken before the CI agent
ran: what an eval may change, and how anything it may not keep is put back. Over the file-system gateway."""
from ..domain.policy import REPO_DIR


class TuningFiles:
    def __init__(self, fs, workspace, snapshot_dir):
        self.fs, self.live, self.snap = fs, fs.join(workspace, REPO_DIR), snapshot_dir

    def _text(self, path):
        return self.fs.read_text(path) if self.fs.is_file(path) else None

    def _clear(self, path):
        if self.fs.is_dir(path) and not self.fs.is_link(path):
            self.fs.remove_tree(path)
        elif self.fs.lexists(path):
            self.fs.remove(path)

    def snapshot(self):
        if self.fs.is_dir(self.live):
            self.fs.copy_tree(self.live, self.snap)

    def current(self, name):
        return self._text(self.fs.join(self.live, name))

    def original(self, name):
        return self._text(self.fs.join(self.snap, name))

    def restore(self, name):
        live, orig = self.fs.join(self.live, name), self.fs.join(self.snap, name)
        if self.fs.is_file(orig):
            if not self.fs.is_file(live) or not self.fs.same_content(live, orig):
                self._clear(live)
                self.fs.make_dirs(self.fs.parent(live))
                self.fs.copy_file(orig, live)
        else:
            self._clear(live)

    def restore_others(self, keep):
        restored = set()
        for top, in_snapshot in ((self.live, False), (self.snap, True)):
            for rel in self.fs.files_under(top):
                if rel in keep:
                    continue
                live, orig = self.fs.join(self.live, rel), self.fs.join(self.snap, rel)
                if not in_snapshot and not self.fs.exists(orig):
                    self.fs.remove(live)                                   # created by the agent
                elif not self.fs.exists(live) or not self.fs.same_content(live, orig):
                    self.fs.make_dirs(self.fs.parent(live))
                    self.fs.copy_file(orig, live)                          # changed or removed by the agent
                else:
                    continue
                restored.add(rel)
        return sorted(restored)
