# GUES10Man

A Discord bot for running 10 man custom games on the uni esports server. Players join a queue with a button, and when it fills the bot opens a match. The ten players then choose between an automatically balanced split and a captains draft. Once teams are set, the bot picks a map and pings everyone with the team they're on. After the game, a captain or staff member reports the result and Elo is updated. Several matches can run at once: each match has its own number, and the queue keeps filling for the next lobby while earlier games are still being played.

Valorant is the main game. Counter-Strike 2, League of Legends and Overwatch 2 are also set up, and new games can be added in `gues10man/games.py`. Ratings, records and leaderboards are kept separately for each game.

## Getting started

You need Python 3.11 or newer.

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in DISCORD_TOKEN and HENRIK_API_KEY
python -m gues10man
```

When you create the application in the [Discord developer portal](https://discord.com/developers/applications), invite the bot with the `bot` and `applications.commands` scopes. It needs Send Messages and Embed Links. It doesn't use any privileged intents. Set `GUILD_ID` to the server's ID so that slash command changes appear straight away rather than after Discord's global sync delay. Several servers can be listed with commas, for example a test server and the real one.

Once the bot is in the server, a member with Manage Server should run:

- `/setup staffrole role:<role>` so committee members can run queues without needing Manage Server

Whoever owns the bot in the developer portal always counts as staff, as does anyone listed in `STAFF_USER_IDS` in `.env`. That way you can test on a server where you don't have Manage Server.
- `/queue open game:Valorant` in the channel where the queue should live

## How a match runs

The queue message has Join and Leave buttons and shows each player's Elo. The queue is stored in SQLite, so it survives a bot restart. When the tenth person joins, the first ten in the queue are taken into a new match and the queue is free for the next lobby.

The match opens with a 10 second map vote: three maps from the game's pool appear as buttons, each player in the match gets one vote (they can change it), and the most-voted map wins, with ties settled at random. After that, the match message lets any of the ten choose how teams are made:

**Auto balance** looks at all 126 possible 5v5 splits and finds the one with the smallest gap in average Elo. It then picks at random from every split within 15 Elo of that best one, so the same group won't get identical teams every week. The highest rated player on each side becomes captain for reporting purposes.

**Captains draft** uses either the two highest rated players or two random players as captains. The lower rated captain picks first, and picks follow a snake order (A B B A A B B A). When the draft starts, each captain gets a DM with the player pool sorted by Elo. Each entry shows approximate rank, record and Riot ID, along with which pick numbers are theirs and a link back to the match. If a captain's DMs are closed, the bot says so in the channel. Picks are made on the match message so everyone can follow along, and only the captain whose turn it is, or staff, can use the picker. Drafts are held in memory, so a draft cut off by a restart goes back to the mode choice.

Once teams are locked in, a coin flip decides which team starts on attack and which on defence (Terrorists and Counter-Terrorists in CS2, blue and red side in League). The bot shows the voted map (or a random one for games without a map vote), the predicted win chance, and posts a reply pinging each side by match number, so players in overlapping games know which lobby and side they're on. Captains are marked with (C). Captains can reroll the map. Either captain or staff reports the winner with a button, optionally entering the round score, and Elo changes are posted on the match message. `/match report` does the same from a command: name any player on the winning team rather than picking a side.

For Valorant, when `HENRIK_API_KEY` is set, a live match also has a **Game finished** button. When a captain presses it, the bot looks the custom game up in Riot's match history through the linked Riot IDs. It accepts a game only if it is a standard 5v5 that started after the match went live, at least two (and 60%) of the linked players appear in it, and each of our teams is entirely on one side. It then records the winner, score, real map and every linked player's ACS. Riot can take a few minutes to publish a game, so the bot checks once a minute for up to ten minutes and updates a status message as it goes. `/match import` does the same from a command, and the result buttons still work if a game can't be found.

Staff can cancel a match at any stage. They can swap a player out with `/match sub`, and `/match undo` reverses a mistaken result, putting the match back to live so it can be reported again.

## Elo

Ratings come from a team Bradley-Terry model (`gues10man/elo5v5.py`). It is fitted over the whole match history, rather than nudged a little after each game. A team's strength is the product of its players' strengths, which in Elo terms is the sum of their ratings. The fit uses the minorisation-maximisation algorithm to find the ratings that best explain every recorded result. After each report, the whole game's history is refitted in a background thread, so a single match can shift the ratings of people who weren't in it once the model learns more about their past opponents and teammates. Undoing a result is exact: the match is removed and everything is refitted.

Each player has a prior: a virtual win and a virtual loss against a player at their seed rating. A brand new player therefore sits exactly on their seed, and real results take over as games accumulate. Running `/link riot_id:User#1234` sets the seed from their Valorant rank, stepping 60 points per division from Iron 1 (600) to Radiant (2040), so Gold 1 is 1140 and Ascendant 1 is 1680. If the account is unranked this act, the bot uses the peak rank minus three divisions. Players without a linked account can use `/setrank` before their first game. Everyone else starts at 1000. Staff can move a seed with `/rating seed` or `/rating adjust`.

The model's own numbers are on a "team sum" scale, where a 400-point gap between team totals means 10 to 1 odds. The bot shows `1000 + 5 × fitted rating` instead. That keeps the familiar rank-based scale and means the gap between two teams' *average* Elo reads like ordinary Elo: 100 points is roughly 64%.

After a match, captains can enter each team's scoreboard (ACS for Valorant) using the buttons on the result. Each team's scoreboard is treated as a Plackett-Luce ranking, in which the top scorer beats their four teammates, the second beats the remaining three, and so on. This lets the model tell apart players who always win together. Only the order of the scores matters, not their size. `PRIOR` and `PERF_WEIGHT` in `gues10man/rating.py` control how strongly seeds and scoreboards count. They were chosen by replaying simulated leagues and scoring how well each setting predicted the next game. With a prior of 1, one win was worth about 185 points and early predictions were worse than a coin flip. A prior of 20 with a performance weight of 0.3 did best, and a single even win is then worth about 35 points. The simulation's assumptions about skill spread and how much ACS reflects skill are guesses, so revisit these once there is real data.

## Profiles and leaderboards

`/elo help` explains the rating system to players in plain language and cites the two papers it is based on: Coulom's "Computing Elo Ratings of Move Patterns in the Game of Go" (ICGA Journal, 2007) and Hunter's "MM algorithms for generalized Bradley-Terry models" (Annals of Statistics, 2004).

`/profile` shows a player's Elo with its approximate in-game rank, peak Elo, record and win rate, captain record, recent form and current streak, their record on each map, and the teammates they win with most often. `/history` lists recent matches with score, map and Elo change. `/leaderboard` can be sorted by Elo, wins or win rate, with a minimum games filter, and pages through larger servers.

## Rank lookups and tracker.gg

Tracker.gg doesn't offer a public Valorant API, and its pages sit behind Cloudflare and its terms forbid scraping, so the bot uses the community [HenrikDev Valorant API](https://api.henrikdev.xyz) instead. It takes the same `Name#Tag` input and returns current and peak rank. A free key from its dashboard is plenty for a uni server. If no key is set, `/link` explains that and players fall back to `/setrank`.

## Trying it out

Staff can run `/test run` in any text channel to watch a full session play out with fake players. Ten bot players (Bot Ace to Bot Jett, seeded from Silver 2 to Ascendant 3) and you fill a lobby. The first game goes through a captains draft, with picks appearing one at a time, followed by a simulated result. Three more lobbies then use auto balance and include a simulated ACS scoreboard. Each fake player has a fixed hidden skill near their seed, so repeated runs tell a consistent story. Afterwards, `/leaderboard`, `/profile` and `/history` with `game:Sandbox` show the results.

Everything happens under a separate Sandbox game, so real ratings are never touched. `/test run include_me:False` uses fake players only, `step_seconds` controls how fast it plays, and `/test reset` wipes the sandbox.

### Pulling custom games from the API

To check that custom games can be read from someone's Valorant match history, run this from the terminal once `HENRIK_API_KEY` is in `.env`:

```bash
python -m gues10man.tools.customs "User#1234" --region eu
```

It lists recent custom games with map, score, winner and each player's agent, ACS and K/D/A. In Discord, `/test customs riot_id:User#1234` shows the same thing to you privately, and mentions any player who has linked that Riot ID with `/link`. If nothing comes back or the fields look wrong, run the tool with `--shape`. That prints the layout of the API's response without any names or numbers, so it is safe to share when adjusting the parser in `gues10man/customs.py`.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The code is laid out as follows: `elo.py` and `balance.py` hold the rating maths and team splitting with no Discord dependencies, `db.py` is the SQLite layer, `ranks.py` is the rank lookup client, and the Discord side lives in `cogs/` (`lobby.py` for queues and matches, `players.py` for profiles and leaderboards, `admin.py` for configuration).

Some natural next steps are a ready check when the queue pops, map vetoes run by the captains, and pulling per-player stats (ACS, K/D) for custom games from the HenrikDev match history endpoint to fill out the performance profile.
