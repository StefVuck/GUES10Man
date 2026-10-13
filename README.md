# GUES10Man

A Discord bot for running 10 man custom games on the uni esports server. Players join a queue with a button, and when it fills the bot opens a match. The ten players then choose between an automatically balanced split and a captains draft. Once teams are set, the bot picks a map and moves everyone into their team voice channels. After the game, a captain or staff member reports the result, Elo is updated and everyone is moved back to the lobby.

Valorant is the main game. Counter-Strike 2, League of Legends and Overwatch 2 are also set up, and new games can be added in `gues10man/games.py`. Ratings, records and leaderboards are kept separately for each game.

## Getting started

You need Python 3.11 or newer.

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in DISCORD_TOKEN and HENRIK_API_KEY
python -m gues10man
```

When you create the application in the [Discord developer portal](https://discord.com/developers/applications), invite the bot with the `bot` and `applications.commands` scopes. It needs Send Messages, Embed Links and Move Members. It doesn't use any privileged intents. While you're developing, set `GUILD_ID` to the server's ID so that slash command changes appear straight away rather than after Discord's global sync delay.

Once the bot is in the server, a member with Manage Server should run:

- `/setup voice lobby:<vc> team_a:<vc> team_b:<vc>` to choose the channels players are moved between
- `/setup staffrole role:<role>` so committee members can run queues without needing Manage Server
- `/queue open game:Valorant` in the channel where the queue should live

## How a match runs

The queue message has Join and Leave buttons and shows each player's Elo. The queue is stored in SQLite, so it survives a bot restart. When the tenth person joins, the first ten in the queue are taken into a new match and the queue is free for the next lobby.

The match message lets any of the ten choose how teams are made:

**Auto balance** looks at all 126 possible 5v5 splits and finds the one with the smallest gap in average Elo. It then picks at random from every split within 15 Elo of that best one, so the same group won't get identical teams every week. The highest rated player on each side becomes captain for reporting purposes.

**Captains draft** uses either the two highest rated players or two random players as captains. The lower rated captain picks first, and picks follow a snake order (A B B A A B B A). Only the captain whose turn it is, or staff, can use the picker. Drafts are held in memory, so a draft cut off by a restart goes back to the mode choice.

Once teams are locked in, the bot picks a random map from the game's pool, shows the predicted win chance and moves players who are already in voice into the team channels. Captains can reroll the map. Either captain or staff reports the winner with a button, optionally entering the round score, and Elo changes are posted on the match message.

Staff can cancel a match at any stage. They can swap a player out with `/match sub`, and `/match undo` reverses a mistaken result, putting the match back to live so it can be reported again.

## Elo

Ratings come from a team Bradley-Terry model (`gues10man/elo5v5.py`). It is fitted over the whole match history, rather than nudged a little after each game. A team's strength is the product of its players' strengths, which in Elo terms is the sum of their ratings. The fit uses the minorisation-maximisation algorithm to find the ratings that best explain every recorded result. After each report, the whole game's history is refitted in a background thread, so a single match can shift the ratings of people who weren't in it once the model learns more about their past opponents and teammates. Undoing a result is exact: the match is removed and everything is refitted.

Each player has a prior: a virtual win and a virtual loss against a player at their seed rating. A brand new player therefore sits exactly on their seed, and real results take over as games accumulate. Running `/link riot_id:Arsenic#9738` sets the seed from their Valorant rank, stepping 60 points per division from Iron 1 (600) to Radiant (2040), so Gold 1 is 1140 and Ascendant 1 is 1680. If the account is unranked this act, the bot uses the peak rank minus three divisions. Players without a linked account can use `/setrank` before their first game. Everyone else starts at 1000. Staff can move a seed with `/rating seed` or `/rating adjust`.

The model's own numbers are on a "team sum" scale, where a 400-point gap between team totals means 10 to 1 odds. The bot shows `1000 + 5 × fitted rating` instead. That keeps the familiar rank-based scale and means the gap between two teams' *average* Elo reads like ordinary Elo: 100 points is roughly 64%.

After a match, captains can enter each team's scoreboard (ACS for Valorant) using the buttons on the result. Each team's scoreboard is treated as a Plackett-Luce ranking, in which the top scorer beats their four teammates, the second beats the remaining three, and so on. This lets the model tell apart players who always win together. Only the order of the scores matters, not their size. `PRIOR` and `PERF_WEIGHT` in `gues10man/rating.py` control how strongly seeds and scoreboards count.

## Profiles and leaderboards

`/profile` shows a player's Elo with its approximate in-game rank, peak Elo, record and win rate, captain record, recent form and current streak, their record on each map, and the teammates they win with most often. `/history` lists recent matches with score, map and Elo change. `/leaderboard` can be sorted by Elo, wins or win rate, with a minimum games filter, and pages through larger servers.

## Rank lookups and tracker.gg

Tracker.gg doesn't offer a public Valorant API, and its pages sit behind Cloudflare and its terms forbid scraping, so the bot uses the community [HenrikDev Valorant API](https://api.henrikdev.xyz) instead. It takes the same `Name#Tag` input and returns current and peak rank. A free key from its dashboard is plenty for a uni server. If no key is set, `/link` explains that and players fall back to `/setrank`.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The code is laid out as follows: `elo.py` and `balance.py` hold the rating maths and team splitting with no Discord dependencies, `db.py` is the SQLite layer, `ranks.py` is the rank lookup client, and the Discord side lives in `cogs/` (`lobby.py` for queues and matches, `players.py` for profiles and leaderboards, `admin.py` for configuration).

Some natural next steps are a ready check when the queue pops, map vetoes run by the captains, and pulling per-player stats (ACS, K/D) for custom games from the HenrikDev match history endpoint to fill out the performance profile.
