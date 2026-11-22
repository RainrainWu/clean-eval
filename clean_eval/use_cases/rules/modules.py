"""Group source files into sub-modules (top-level folders, split level by level down to a depth), and find the module a
path belongs to."""

ROOT_MODULE = "(root)"
FILES_SUFFIX = "/(files)"


def _descend(name, files):
    depth = name.count("/") + 1
    while True:
        kids = {f.path.split("/")[depth] for f in files if f.path.count("/") > depth}
        direct = [f for f in files if f.path.count("/") == depth]
        if len(kids) == 1 and not direct:
            depth += 1
            name = "/".join(files[0].path.split("/")[:depth])
            continue
        return name, depth, kids


def _children(files, name, depth):
    out = {}
    for f in files:
        parts = f.path.split("/")
        key = "/".join(parts[: depth + 1]) if len(parts) > depth + 1 else name + FILES_SUFFIX
        out.setdefault(key, []).append(f)
    return out


def _loc(files):
    return sum(f.loc for f in files)


def split_modules(src, policy, depth=None, max_modules=None):
    """Level 1 is the top-level folders. Each further level splits every module of `split_min_loc`+ lines into its
    sub-folders (a single-child chain counts as one step) or, in a flat folder of 2-`split_max_files` files, into one
    module per file. Largest modules split first, and no split may take the total past `max_modules`.
    Returns ({name: files}, notes)."""
    depth = policy.depth if depth is None else depth
    max_modules = policy.max_modules if max_modules is None else max_modules
    mods = {}
    for f in src:
        parts = f.path.split("/")
        mods.setdefault(parts[0] if len(parts) > 1 else ROOT_MODULE, []).append(f)
    notes = []
    for level in range(2, depth + 1):
        out, count = {}, len(mods)
        for name, files in sorted(mods.items(), key=lambda kv: -_loc(kv[1])):
            if name == ROOT_MODULE or name.endswith(FILES_SUFFIX) or _loc(files) < policy.split_min_loc:
                out[name] = files
                continue
            base, d, kids = _descend(name, files)
            if not kids and 2 <= len(files) <= policy.split_max_files:
                new, what = {f.path: [f] for f in files}, "one module per file, %d files" % len(files)
            elif len(kids) >= 2:
                new = _children(files, base, d)
                what = "%d modules" % len(new)
            else:
                out[name] = files
                continue
            if count - 1 + len(new) > max_modules:
                out[name] = files
                continue
            out.update(new)
            count += len(new) - 1
            notes.append("level %d: split %s (%s)" % (level, name, what))
        mods = out
    return mods, notes


def module_of(path, names):
    """The rated module a path belongs to, or None: the file itself when it is a module, else the deepest folder module
    that contains it (`<dir>/(files)` for files directly in a split folder), else `(root)` for a top-level file."""
    if path in names:
        return path
    parts = path.split("/")
    if len(parts) == 1:
        return ROOT_MODULE if ROOT_MODULE in names else None
    for i in range(len(parts) - 1, 0, -1):
        prefix = "/".join(parts[:i])
        if i == len(parts) - 1 and prefix + FILES_SUFFIX in names:
            return prefix + FILES_SUFFIX
        if prefix in names:
            return prefix
    return None
