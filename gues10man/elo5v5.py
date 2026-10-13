import math
from collections import defaultdict


def fit(matches, prior=1.0, perf_weight=0.0, max_iter=500, tol=1e-6, anchors=None):
    """matches: iterable of (winners, losers[, weight[, scores]]).
    scores maps player -> performance score for that match. Each team's
    scoreboard is treated as a Plackett-Luce ranking: the top scorer beats
    the other four, the second beats the remaining three, and so on.
    perf_weight scales those stage results relative to the match result.
    anchors maps player -> elo that their prior is centred on (default 0),
    i.e. the prior is a virtual win and loss against a player of that elo.
    Returns {player: elo}."""
    weights, apps, wins, gamma = [], defaultdict(list), defaultdict(float), {}
    stage_w, stage_sum, stage_apps = [], [], defaultdict(list)

    for j, m in enumerate(matches):
        w, l = list(m[0]), list(m[1])
        weight = m[2] if len(m) > 2 else 1.0
        scores = m[3] if len(m) > 3 else None
        weights.append(weight)
        for side, members in enumerate((w, l)):
            for p in members:
                apps[p].append((j, side))
                gamma[p] = 1.0
        for p in w:
            wins[p] += weight

        if scores and perf_weight > 0:
            sw = weight * perf_weight
            for members in (w, l):
                ranked = sorted((p for p in members if p in scores), key=lambda p: -scores[p])
                for k in range(len(ranked) - 1):
                    s = len(stage_w)
                    stage_w.append(sw)
                    stage_sum.append(float(len(ranked) - k))
                    wins[ranked[k]] += sw
                    for p in ranked[k:]:
                        stage_apps[p].append(s)

    strength = [[1.0, 1.0] for _ in weights]
    anchors = anchors or {}
    anchor_g = {p: 10 ** (anchors.get(p, 0.0) / 400) for p in gamma}

    for _ in range(max_iter):
        worst = 0.0
        for p, ps in apps.items():
            g = gamma[p]
            denom = 2 * prior / (g + anchor_g[p])
            for j, side in ps:
                s = strength[j]
                denom += weights[j] * (s[side] / g) / (s[0] + s[1])
            for s in stage_apps[p]:
                denom += stage_w[s] / stage_sum[s]
            new = (wins[p] + prior) / denom
            ratio = new / g
            for j, side in ps:
                strength[j][side] *= ratio
            for s in stage_apps[p]:
                stage_sum[s] += new - g
            gamma[p] = new
            worst = max(worst, abs(math.log(ratio)))
        if worst < tol:
            break

    return {p: 400 * math.log10(g) for p, g in gamma.items()}


def win_prob(elo, team_a, team_b):
    d = sum(elo.get(p, 0.0) for p in team_a) - sum(elo.get(p, 0.0) for p in team_b)
    return 1 / (1 + 10 ** (-d / 400))
