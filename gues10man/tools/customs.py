"""Check that custom games can be pulled for a Riot ID, without starting the bot.

    python -m gues10man.tools.customs "User#1234" --region eu
    python -m gues10man.tools.customs "User#1234" --shape     # structure only, safe to share
    python -m gues10man.tools.customs "User#1234" --raw out.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from dotenv import load_dotenv

from ..customs import json_shape
from ..ranks import HenrikClient, RankLookupError


async def run(args: argparse.Namespace) -> int:
    client = HenrikClient(os.getenv("HENRIK_API_KEY"))
    try:
        games, payload, version = await client.fetch_custom_matches(args.riot_id, args.region, args.count)
    except RankLookupError as e:
        print(f"Request failed: {e}")
        return 1
    finally:
        await client.close()

    standard = sum(g.is_standard for g in games)
    print(f"Answered by the {version} endpoint: {len(games)} custom game(s), {standard} of them standard 5v5.\n")
    if args.raw:
        with open(args.raw, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"Raw response written to {args.raw}. It contains other players' names, so share the --shape output instead.\n")
    if args.shape:
        print(json_shape(payload))
        return 0

    for g in games:
        when = g.started_at.strftime("%Y-%m-%d %H:%M") if g.started_at else "unknown time"
        score = " - ".join(f"{t} {g.rounds_won.get(t, '?')}" for t in g.team_names)
        tag = "5v5, would count" if g.is_standard else f"{g.kind}, would be ignored"
        print(f"{when}  {g.map or '?'}  {score}  winner: {g.winner or '?'}  ({g.rounds} rounds, {tag})")
        for team in g.team_names:
            for p in g.team(team):
                acs = g.acs(p)
                acs_text = f"{acs:>5.0f} ACS" if acs is not None else "  n/a ACS"
                print(f"    {team:<5} {p.riot_id:<24} {p.agent or '?':<10} {acs_text}  {p.kills}/{p.deaths}/{p.assists}")
        print()
    if not games:
        print("No custom games came back. Play a custom recently, or run with --shape to check the response layout.")
    return 0


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("riot_id", help="Name#Tag, for example User#1234")
    parser.add_argument("--region", default=os.getenv("VALORANT_REGION", "eu"))
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--shape", action="store_true", help="print the response structure without values")
    parser.add_argument("--raw", metavar="FILE", help="save the full JSON response")
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
