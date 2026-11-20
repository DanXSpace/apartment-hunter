"""Small pieces of text formatting shared by the Criteria, the Sources, the runner and the bot's replies."""

from collections.abc import Sequence


def plain_number(n: float) -> str:
    """2 and 2.0 both read "2"; 1.5 stays "1.5"."""
    return str(int(n)) if float(n).is_integer() else str(n)


def places_match(n: int) -> str:
    return f"{n:,} {'place matches' if n == 1 else 'places match'}"


def join_and(items: Sequence[str]) -> str:
    """ "A", "A and B", "A, B and C"."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def time_ago(seconds: float) -> str:
    """ "just now" under a minute, then "N min ago", "N h ago" and "N days ago", each rounded."""
    minutes = round(seconds / 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours = round(minutes / 60)
    return f"{hours} h ago" if hours < 24 else f"{round(hours / 24)} days ago"
