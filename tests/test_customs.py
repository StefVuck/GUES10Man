from gues10man.customs import json_shape, parse_matches

V4 = {
    "data": [
        {
            "metadata": {
                "match_id": "abc",
                "map": {"id": "x", "name": "Ascent"},
                "queue": {"id": "custom", "name": "Custom"},
                "started_at": "2026-10-12T19:00:00.000Z",
            },
            "players": [
                {"name": "User", "tag": "1234", "team_id": "Red", "agent": {"name": "Jett"},
                 "stats": {"score": 4600, "kills": 20, "deaths": 12, "assists": 4}},
                {"name": "Other", "tag": "EUW", "team_id": "Blue", "agent": {"name": "Sova"},
                 "stats": {"score": 2300, "kills": 9, "deaths": 15, "assists": 7}},
            ],
            "teams": [
                {"team_id": "Red", "won": True, "rounds": {"won": 13, "lost": 7}},
                {"team_id": "Blue", "won": False, "rounds": {"won": 7, "lost": 13}},
            ],
            "rounds": [{}] * 20,
        }
    ]
}

V3 = {
    "data": [
        {
            "metadata": {"matchid": "def", "map": "Lotus", "mode": "Custom Game", "game_start": 1760295600, "rounds_played": 24},
            "players": {"all_players": [
                {"name": "User", "tag": "1234", "team": "Blue", "character": "Omen",
                 "stats": {"score": 4800, "kills": 18, "deaths": 16, "assists": 9}},
            ]},
            "teams": {"red": {"has_won": False, "rounds_won": 11}, "blue": {"has_won": True, "rounds_won": 13}},
        }
    ]
}


def test_parse_v4():
    (g,) = parse_matches(V4)
    assert (g.map, g.mode, g.rounds, g.winner) == ("Ascent", "Custom", 20, "Red")
    assert g.rounds_won == {"Red": 13, "Blue": 7}
    top = g.team("Red")[0]
    assert top.riot_id == "User#1234" and top.agent == "Jett" and g.acs(top) == 230
    assert g.started_at.year == 2026


def test_parse_v3():
    (g,) = parse_matches(V3)
    assert (g.map, g.winner, g.rounds) == ("Lotus", "Blue", 24)
    assert g.team("Blue")[0].agent == "Omen" and g.acs(g.team("Blue")[0]) == 200


def test_empty_and_odd_payloads():
    assert parse_matches({}) == []
    assert parse_matches({"data": [{}]})[0].players == []


def test_shape_hides_values():
    shape = json_shape(V4)
    assert "name: str" in shape and "User" not in shape and "Ascent" not in shape
