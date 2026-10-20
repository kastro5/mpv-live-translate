"""Which windows to work on next. Pure, so the policy can be tested without audio or APIs."""


def window_index(starts, t):
    """Index of the window containing t, given each window's start time (sorted)."""
    idx = 0
    for i, start in enumerate(starts):
        if start <= t:
            idx = i
    return idx


def wanted(starts, position, mode, lookahead_s, finished):
    """Window indices in priority order: from the playhead up to the lookahead horizon;
    in "full" mode then the rest of the file, then everything before the playhead.
    `finished` holds indices that are done (or failed) and never come back."""
    first = window_index(starts, position)
    horizon = position + lookahead_s
    ahead = [i for i in range(first, len(starts)) if starts[i] < horizon]
    order = ahead
    if mode == "full":
        order = ahead + list(range(first + len(ahead), len(starts))) + list(range(first))
    return [i for i in order if i not in finished]


def to_start(order, running, slots):
    """The first windows of `order` not already running, up to the free slots."""
    free = max(0, slots - len(running))
    return [i for i in order if i not in running][:free]
