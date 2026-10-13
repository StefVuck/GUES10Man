from datetime import datetime, timedelta, timezone

from gues10man.customs import CustomGame, CustomPlayer
from gues10man.importer import match_game

NOW = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)
RED = [f"r{i}#tag" for i in range(5)]
BLUE = [f"b{i}#tag" for i in range(5)]


def game(red=RED, blue=BLUE, winner="Red", started=NOW + timedelta(minutes=5), rounds_won=(13, 9)):
    players = [CustomPlayer(*r.split("#"), "Red", "Jett", 4400 - i * 400, 0, 0, 0) for i, r in enumerate(red)]
    players += [CustomPlayer(*b.split("#"), "Blue", "Sova", 4000, 0, 0, 0) for b in blue]
    return CustomGame("id", "Ascent", "Custom Game", started, sum(rounds_won),
                      {"Red": rounds_won[0], "Blue": rounds_won[1]}, winner, players)


def test_finds_game_and_maps_sides():
    found = match_game([game()], set(BLUE[:3]), set(RED[:3]), NOW)
    assert found.winner == "B" and found.score == (9, 13)
    assert round(found.acs["r0#tag"]) == 200


def test_one_sided_linking_still_works():
    found = match_game([game(winner="Blue", rounds_won=(9, 13))], set(RED[:3]), set(), NOW)
    assert found.winner == "B" and found.score == (9, 13)


def test_rejects_games_before_match_went_live():
    old = game(started=NOW - timedelta(hours=2))
    assert match_game([old], set(RED[:3]), set(BLUE[:3]), NOW) is None


def test_rejects_mixed_teams():
    mixed = game(red=RED[:3] + BLUE[:2], blue=BLUE[2:] + RED[3:])
    assert match_game([mixed], set(RED), set(BLUE), NOW) is None


def test_rejects_when_too_few_linked_players_present():
    assert match_game([game()], {"x#1", "y#2", "z#3", RED[0]}, set(), NOW) is None


def test_rejects_non_standard_games():
    skirmish = game(red=RED[:2], blue=BLUE[:2])
    assert match_game([skirmish], set(RED[:2]), set(BLUE[:2]), NOW) is None


def test_prefers_game_with_most_linked_players():
    partial = game(red=RED[:3] + ["u1#t", "u2#t"], winner="Blue")
    full = game(started=NOW + timedelta(minutes=40))
    found = match_game([partial, full], set(RED), set(BLUE), NOW)
    assert found.game is full and found.winner == "A"
