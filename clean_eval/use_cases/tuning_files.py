"""The repository's tuning files (`.clean-eval/` in the working tree) and the snapshot of them taken before the CI agent
ran, through the FileSystem interface: what an eval may change, and how anything it may not keep is put back."""
from clean_eval.domain.policy import REPO_DIR

SNAPSHOT = "clean-eval.orig"  # in the context folder: .clean-eval/ as it was before the agent ran


def _text(fs, path):
    return fs.read_text(path) if fs.is_file(path) else None


def _clear(fs, path):
    if fs.is_dir(path) and not fs.is_link(path):
        fs.remove_tree(path)
    elif fs.lexists(path):
        fs.remove(path)


def snapshot_tuning(fs, workspace, snapshot_dir):
    """Copy .clean-eval/ aside, as it is now."""
    live = fs.join(workspace, REPO_DIR)
    if fs.is_dir(live):
        fs.copy_tree(live, snapshot_dir)


def current_tuning(fs, workspace, name):
    """The text of .clean-eval/<name> now, or None when there is no such file."""
    return _text(fs, fs.join(workspace, REPO_DIR, name))


def original_tuning(fs, snapshot_dir, name):
    """The text of .clean-eval/<name> in the snapshot, or None when there was no such file."""
    return _text(fs, fs.join(snapshot_dir, name))


def restore_tuning(fs, workspace, snapshot_dir, name):
    """Put .clean-eval/<name> back as it was in the snapshot (removed when it wasn't there)."""
    live, orig = fs.join(workspace, REPO_DIR, name), fs.join(snapshot_dir, name)
    if fs.is_file(orig):
        if not fs.is_file(live) or not fs.same_content(live, orig):
            _clear(fs, live)
            fs.make_dirs(fs.parent(live))
            fs.copy_file(orig, live)
    else:
        _clear(fs, live)


def restore_other_tuning(fs, workspace, snapshot_dir, keep):
    """Put every file under .clean-eval/ except `keep` back as it was; the paths that needed it, sorted."""
    live_dir, restored = fs.join(workspace, REPO_DIR), set()
    for top, in_snapshot in ((live_dir, False), (snapshot_dir, True)):
        for rel in fs.files_under(top):
            if rel in keep:
                continue
            live, orig = fs.join(live_dir, rel), fs.join(snapshot_dir, rel)
            if not in_snapshot and not fs.exists(orig):
                fs.remove(live)                                     # created by the agent
            elif not fs.exists(live) or not fs.same_content(live, orig):
                fs.make_dirs(fs.parent(live))
                fs.copy_file(orig, live)                            # changed or removed by the agent
            else:
                continue
            restored.add(rel)
    return sorted(restored)
