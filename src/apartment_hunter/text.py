"""Small pieces of user-facing copy shared by the runner and the bot's replies."""


def places_match(n: int) -> str:
    return f"{n:,} {'place matches' if n == 1 else 'places match'}"
