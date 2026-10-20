import numpy as np


def dk_points(points, threes, rebounds, assists, steals, blocks, turnovers):
    """Classic scoring; DD and TD bonuses stack, each at most once."""
    categories = np.stack([points, rebounds, assists, steals, blocks], axis=-1)
    doubles = (categories >= 10).sum(axis=-1)
    return (points + .5 * threes + 1.25 * rebounds + 1.5 * assists
            + 2 * steals + 2 * blocks - .5 * turnovers
            + 1.5 * (doubles >= 2) + 3 * (doubles >= 3))
