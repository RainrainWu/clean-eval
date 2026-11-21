"""Squarified treemap layout."""


def squarify(items, x, y, w, h):
    """`items` is [(payload, area)] sorted by area descending; returns [(payload, x, y, w, h)] filling the rectangle."""
    out, rest = [], list(items)

    def worst(row, side):
        s, mx, mn = sum(a for _, a in row), max(a for _, a in row), min(a for _, a in row)
        return max(side * side * mx / (s * s), s * s / (side * side * mn))

    while rest:
        side, row, i = min(w, h), [rest[0]], 1
        while i < len(rest) and worst(row + [rest[i]], side) <= worst(row, side):
            row.append(rest[i])
            i += 1
        s = sum(a for _, a in row)
        if w >= h:
            cw, cy = s / h, y
            for p, a in row:
                out.append((p, x, cy, cw, a / cw))
                cy += a / cw
            x, w = x + cw, w - cw
        else:
            rh, cx = s / w, x
            for p, a in row:
                out.append((p, cx, y, a / rh, rh))
                cx += a / rh
            y, h = y + rh, h - rh
        rest = rest[len(row):]
    return out
