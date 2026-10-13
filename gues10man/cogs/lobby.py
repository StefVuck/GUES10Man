from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..balance import auto_balance, choose_captains, snake_order
from ..elo import PlayerRating, win_probability
from ..games import DEFAULT_RATING, GAMES, Game
from ..util import FAKE_PLAYERS, GAME_CHOICES, missing_channel_permissions, mention, fmt_delta, fmt_rating, is_staff, reply, staff_only

if TYPE_CHECKING:
    from ..bot import TenManBot

log = logging.getLogger(__name__)

COLOUR = discord.Colour(0xFF4655)


@dataclass
class DraftState:
    captains: tuple[int, int]
    order: list[int]
    teams: tuple[list[int], list[int]]
    pool: list[int]
    pick_number: int = 0
    pick_log: dict[int, int] = field(default_factory=dict)

    @property
    def current_captain(self) -> int:
        return self.captains[self.order[self.pick_number]]

    def pick(self, user_id: int) -> None:
        side = self.order[self.pick_number]
        self.teams[side].append(user_id)
        self.pool.remove(user_id)
        self.pick_number += 1
        self.pick_log[user_id] = self.pick_number

    @property
    def done(self) -> bool:
        return not self.pool


def lobby(interaction: discord.Interaction) -> Lobby:
    return interaction.client.get_cog("Lobby")  # type: ignore[return-value]


class QueueButton(discord.ui.DynamicItem[discord.ui.Button], template=r"q:(?P<action>join|leave):(?P<game>[a-z0-9]+)"):
    def __init__(self, action: str, game: str):
        super().__init__(
            discord.ui.Button(
                label="Join" if action == "join" else "Leave",
                style=discord.ButtonStyle.success if action == "join" else discord.ButtonStyle.secondary,
                custom_id=f"q:{action}:{game}",
            )
        )
        self.action = action
        self.game = game

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], match["game"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await lobby(interaction).handle_queue_button(interaction, self.game, self.action == "join")


MATCH_BUTTONS = {
    "auto": ("Auto balance", discord.ButtonStyle.primary),
    "draft": ("Captains draft (top Elo)", discord.ButtonStyle.primary),
    "draftr": ("Captains draft (random)", discord.ButtonStyle.secondary),
    "done": ("Game finished", discord.ButtonStyle.primary),
    "wina": ("Team A won", discord.ButtonStyle.success),
    "winb": ("Team B won", discord.ButtonStyle.success),
    "reroll": ("Reroll maps", discord.ButtonStyle.secondary),
    "sca": ("Team A scoreboard", discord.ButtonStyle.secondary),
    "scb": ("Team B scoreboard", discord.ButtonStyle.secondary),
    "cancel": ("Cancel match", discord.ButtonStyle.danger),
}


class MatchButton(discord.ui.DynamicItem[discord.ui.Button], template=r"m:(?P<action>[a-z]+):(?P<id>\d+)"):
    def __init__(self, action: str, match_id: int, label: str | None = None):
        default, style = MATCH_BUTTONS[action]
        super().__init__(discord.ui.Button(label=label or default, style=style, custom_id=f"m:{action}:{match_id}"))
        self.action = action
        self.match_id = match_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        if match["action"] not in MATCH_BUTTONS:
            raise ValueError(match["action"])
        return cls(match["action"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await lobby(interaction).handle_match_button(interaction, self.match_id, self.action)


MAP_VOTE_SECONDS = 20
MAP_VOTE_OPTIONS = 3


@dataclass
class MapVote:
    options: list[str]
    ends_at: float
    votes: dict[int, str] = field(default_factory=dict)
    rerolled_by: set[int] = field(default_factory=set)

    def count(self, map_name: str) -> int:
        return sum(v == map_name for v in self.votes.values())

    def winner(self, rng: random.Random | None = None) -> str:
        rng = rng or random.Random()
        top = max(self.count(m) for m in self.options)
        return rng.choice([m for m in self.options if self.count(m) == top])


class MapVoteButton(discord.ui.DynamicItem[discord.ui.Button], template=r"v:(?P<id>\d+):(?P<i>\d)"):
    def __init__(self, match_id: int, index: int, label: str = "Map"):
        super().__init__(
            discord.ui.Button(label=label, style=discord.ButtonStyle.primary, custom_id=f"v:{match_id}:{index}")
        )
        self.match_id = match_id
        self.index = index

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]), int(match["i"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await lobby(interaction).handle_vote(interaction, self.match_id, self.index)


class DraftSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"d:(?P<id>\d+)"):
    def __init__(self, match_id: int, options: list[discord.SelectOption] | None = None):
        super().__init__(
            discord.ui.Select(
                custom_id=f"d:{match_id}",
                placeholder="Captain, pick a player",
                options=options or [discord.SelectOption(label="-", value="0")],
            )
        )
        self.match_id = match_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values") or []
        if values:
            await lobby(interaction).handle_pick(interaction, self.match_id, int(values[0]))


class ResultModal(discord.ui.Modal):
    def __init__(self, cog: Lobby, match_id: int, winner: str, game: Game):
        super().__init__(title=f"Report match #{match_id}: {game.side(winner)} won")
        self.score_a = discord.ui.TextInput(label=f"{game.side('A')} rounds (optional)", required=False, max_length=2)
        self.score_b = discord.ui.TextInput(label=f"{game.side('B')} rounds (optional)", required=False, max_length=2)
        self.add_item(self.score_a)
        self.add_item(self.score_b)
        self.cog = cog
        self.match_id = match_id
        self.winner = winner

    async def on_submit(self, interaction: discord.Interaction) -> None:
        score = None
        if self.score_a.value or self.score_b.value:
            try:
                score = (int(self.score_a.value), int(self.score_b.value))
            except ValueError:
                await reply(interaction, "Scores need to be numbers, or leave both blank.")
                return
        await self.cog.finish_match(interaction, self.match_id, self.winner, score)


class ScoreboardModal(discord.ui.Modal):
    """One number per player on a team. The fit ranks each team's scoreboard,
    so only the order matters, not the scale."""

    def __init__(self, cog: Lobby, match_id: int, team_name: str, players: list[tuple[int, str, float | None]], label: str):
        super().__init__(title=f"Match #{match_id}: {team_name} {label}")
        self.cog = cog
        self.match_id = match_id
        self.inputs: dict[int, discord.ui.TextInput] = {}
        for uid, name, current in players:
            field = discord.ui.TextInput(
                label=name[:45], required=False, max_length=6, default=None if current is None else f"{current:g}"
            )
            self.inputs[uid] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            scores = {uid: float(f.value) for uid, f in self.inputs.items() if f.value.strip()}
        except ValueError:
            await reply(interaction, "Scores need to be numbers.")
            return
        await self.cog.save_scoreboard(interaction, self.match_id, scores)


def not_live(match_id: int, m) -> str:
    status = m["status"] if m else None
    return {
        "complete": f"Match #{match_id} has already finished. Staff can use `/match undo` if the result was wrong.",
        "cancelled": f"Match #{match_id} was cancelled.",
        "setup": f"Match #{match_id} hasn't started yet; teams still need picking.",
        "drafting": f"Match #{match_id} is still drafting.",
    }.get(status, f"Match #{match_id} doesn't exist.")


def queue_view(game: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(QueueButton("join", game))
    view.add_item(QueueButton("leave", game))
    return view


def match_view(match_id: int, actions: list[str], game: Game | None = None) -> discord.ui.View:
    labels = {}
    if game:
        labels = {
            "wina": f"{game.side('A')} won",
            "winb": f"{game.side('B')} won",
            "sca": f"{game.side('A')} scoreboard",
            "scb": f"{game.side('B')} scoreboard",
        }
    view = discord.ui.View(timeout=None)
    for action in actions:
        view.add_item(MatchButton(action, match_id, labels.get(action)))
    return view


class Lobby(commands.Cog):
    queue = app_commands.Group(name="queue", description="Custom game queues", guild_only=True)
    match = app_commands.Group(name="match", description="Manage 10 man matches", guild_only=True)

    def __init__(self, bot: TenManBot):
        self.bot = bot
        self.db = bot.db
        self.locks: defaultdict[object, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.drafts: dict[int, DraftState] = {}
        self.names: dict[int, str] = {}
        self._recovered = False
        self._tasks: set[asyncio.Task] = set()
        self.votes: dict[int, MapVote] = {}

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(QueueButton, MatchButton, DraftSelect, MapVoteButton)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Drafts live in memory, so any interrupted by a restart go back to the mode picker.
        if self._recovered:
            return
        self._recovered = True
        for m in await self.db.active_matches():
            if m["status"] == "drafting":
                await self.db.update_match(m["id"], status="setup", mode=None)
                await self.db.db.execute(
                    "UPDATE match_players SET team = NULL, is_captain = 0, pick_order = NULL WHERE match_id = ?",
                    (m["id"],),
                )
                await self.db.db.commit()
                await self.refresh_match_message(m["id"])
            elif m["status"] == "voting":
                # The vote lived in memory, so pick from the whole pool and start the game.
                await self.go_live(None, m["id"])

    # helpers

    async def name(self, guild: discord.Guild | None, user_id: int) -> str:
        if user_id in FAKE_PLAYERS:
            return FAKE_PLAYERS[user_id]
        if guild is None:
            return str(user_id)
        if user_id not in self.names:
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.HTTPException:
                    return str(user_id)
            self.names[user_id] = member.display_name
        return self.names[user_id]

    async def ratings(self, user_ids: list[int], game: str) -> dict[int, PlayerRating]:
        out = {}
        for uid in user_ids:
            r = await self.db.get_or_create_rating(uid, game, DEFAULT_RATING)
            out[uid] = r.as_player()
        return out

    async def can_manage_match(self, interaction: discord.Interaction, match_id: int, captains_only: bool) -> bool:
        if await is_staff(interaction):
            return True
        players = await self.db.match_players(match_id)
        if captains_only:
            return any(p["user_id"] == interaction.user.id and p["is_captain"] for p in players)
        return any(p["user_id"] == interaction.user.id for p in players)

    # queue

    async def queue_embed(self, guild: discord.Guild, game: Game) -> discord.Embed:
        members = await self.db.queue_members(guild.id, game.key)
        ratings = await self.ratings(members, game.key)
        lines = [
            f"`{i:>2}` {mention(uid)} ({fmt_rating(ratings[uid].rating)})" for i, uid in enumerate(members, 1)
        ]
        embed = discord.Embed(
            title=f"{game.name} 10 man queue",
            description="\n".join(lines) or "*Nobody yet, be the first!*",
            colour=COLOUR,
        )
        embed.set_footer(text=f"{len(members)}/{game.lobby_size} in queue. A match starts when the queue fills.")
        return embed

    async def refresh_queue_message(self, guild: discord.Guild, game_key: str) -> None:
        q = await self.db.get_queue(guild.id, game_key)
        if not q or not q["message_id"]:
            return
        channel = guild.get_channel(q["channel_id"])
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            await channel.get_partial_message(q["message_id"]).edit(
                embed=await self.queue_embed(guild, GAMES[game_key]), view=queue_view(game_key)
            )
        except discord.HTTPException:
            log.warning("Couldn't update queue message for %s", game_key)

    async def handle_queue_button(self, interaction: discord.Interaction, game_key: str, joining: bool) -> None:
        guild = interaction.guild
        assert guild is not None
        game = GAMES[game_key]
        uid = interaction.user.id
        popped: int | None = None

        async with self.locks[(guild.id, game_key)]:
            if not await self.db.get_queue(guild.id, game_key):
                await reply(interaction, "This queue has been closed.")
                return
            if joining:
                active = await self.db.active_match_for(uid)
                if active:
                    await reply(interaction, f"You're already in match #{active}. Finish that one first.")
                    return
                await self.db.get_or_create_rating(uid, game_key, DEFAULT_RATING)
                if not await self.db.join_queue(guild.id, game_key, uid):
                    await reply(interaction, "You're already in the queue.")
                    return
                members = await self.db.queue_members(guild.id, game_key)
                if len(members) >= game.lobby_size:
                    popped = await self.db.create_match(guild.id, game_key, members[: game.lobby_size])
            elif not await self.db.leave_queue(guild.id, game_key, uid):
                await reply(interaction, "You aren't in the queue.")
                return

            await interaction.response.edit_message(embed=await self.queue_embed(guild, game), view=queue_view(game_key))

        if popped is not None and isinstance(interaction.channel, discord.abc.Messageable):
            await self.post_match(interaction.channel, popped)

    @queue.command(name="open", description="Post a queue for a game in this channel")
    @app_commands.choices(game=GAME_CHOICES)
    @staff_only()
    async def queue_open(self, interaction: discord.Interaction, game: str) -> None:
        assert interaction.guild is not None and interaction.channel is not None
        problem = missing_channel_permissions(interaction)
        if problem:
            await reply(interaction, problem)
            return
        existing = await self.db.get_queue(interaction.guild.id, game)
        if existing and existing["message_id"]:
            old = interaction.guild.get_channel(existing["channel_id"])
            if isinstance(old, discord.abc.Messageable):
                try:
                    await old.get_partial_message(existing["message_id"]).delete()
                except discord.HTTPException:
                    pass
        await self.db.set_queue_message(interaction.guild.id, game, interaction.channel.id, None)
        await interaction.response.send_message(
            embed=await self.queue_embed(interaction.guild, GAMES[game]), view=queue_view(game)
        )
        message = await interaction.original_response()
        await self.db.set_queue_message(interaction.guild.id, game, interaction.channel.id, message.id)

    @queue.command(name="close", description="Close a queue and remove everyone from it")
    @app_commands.choices(game=GAME_CHOICES)
    @staff_only()
    async def queue_close(self, interaction: discord.Interaction, game: str) -> None:
        assert interaction.guild is not None
        q = await self.db.get_queue(interaction.guild.id, game)
        if not q:
            await reply(interaction, "That queue isn't open.")
            return
        channel = interaction.guild.get_channel(q["channel_id"])
        if q["message_id"] and isinstance(channel, discord.abc.Messageable):
            try:
                await channel.get_partial_message(q["message_id"]).edit(
                    embed=discord.Embed(title=f"{GAMES[game].name} queue closed", colour=discord.Colour.dark_grey()),
                    view=None,
                )
            except discord.HTTPException:
                pass
        await self.db.delete_queue(interaction.guild.id, game)
        await reply(interaction, "Queue closed.")

    @queue.command(name="kick", description="Remove someone from a queue")
    @app_commands.choices(game=GAME_CHOICES)
    @staff_only()
    async def queue_kick(self, interaction: discord.Interaction, game: str, member: discord.Member) -> None:
        assert interaction.guild is not None
        removed = await self.db.leave_queue(interaction.guild.id, game, member.id)
        await self.refresh_queue_message(interaction.guild, game)
        await reply(interaction, f"Removed {member.mention}." if removed else f"{member.mention} wasn't queued.")

    @queue.command(name="clear", description="Empty a queue")
    @app_commands.choices(game=GAME_CHOICES)
    @staff_only()
    async def queue_clear(self, interaction: discord.Interaction, game: str) -> None:
        assert interaction.guild is not None
        await self.db.clear_queue(interaction.guild.id, game)
        await self.refresh_queue_message(interaction.guild, game)
        await reply(interaction, "Queue cleared.")

    # match rendering

    async def render_match(self, match_id: int) -> tuple[discord.Embed, discord.ui.View | None]:
        m = await self.db.get_match(match_id)
        game = GAMES[m["game"]]
        players = await self.db.match_players(match_id)
        ratings = await self.ratings([p["user_id"] for p in players], game.key)
        status = m["status"]

        def line(p, delta: bool = False) -> str:
            uid = p["user_id"]
            rating = p["rating_before"] if delta and p["rating_before"] is not None else ratings[uid].rating
            text = f"{mention(uid)} ({fmt_rating(rating)})"
            if p["is_captain"]:
                text += " (C)"
            if delta and p["rating_delta"] is not None:
                text += f" **{fmt_delta(p['rating_delta'])}**"
            if delta and p["score"] is not None:
                text += f" · {p['score']:g} {game.score_label}"
            return text

        embed = discord.Embed(title=f"{game.name} match #{match_id}", colour=COLOUR)

        if status == "setup":
            ordered = sorted(players, key=lambda p: ratings[p["user_id"]].rating, reverse=True)
            embed.description = "\n".join(line(p) for p in ordered)
            embed.set_footer(text="Any player in the match can choose how teams are picked.")
            return embed, match_view(match_id, ["auto", "draft", "draftr", "cancel"])

        if status == "drafting":
            state = self.drafts.get(match_id)
            if state is None:
                embed.description = "This draft was interrupted. Staff can cancel the match."
                return embed, match_view(match_id, ["cancel"])
            guild = self.bot.get_guild(m["guild_id"])
            for side in (0, 1):
                members = [next(p for p in players if p["user_id"] == u) for u in state.teams[side]]
                captain = state.captains[side]
                captain_name = await self.name(guild, captain) if guild else str(captain)
                embed.add_field(name=f"Team {captain_name}", value="\n".join(line(p) for p in members) or "-", inline=True)
            embed.add_field(
                name="Available",
                value="\n".join(f"{mention(u)} ({fmt_rating(ratings[u].rating)})" for u in state.pool) or "-",
                inline=False,
            )
            embed.description = (
                f"{mention(state.current_captain)} is picking (pick {state.pick_number + 1} of {len(state.order)}). "
                "Sides are decided by coin flip once the draft is done."
            )
            options = []
            for u in sorted(state.pool, key=lambda u: ratings[u].rating, reverse=True):
                label = await self.name(guild, u) if guild else str(u)
                options.append(discord.SelectOption(label=label[:100], value=str(u), description=f"Elo {fmt_rating(ratings[u].rating)}"))
            view = discord.ui.View(timeout=None)
            view.add_item(DraftSelect(match_id, options))
            view.add_item(MatchButton("cancel", match_id))
            return embed, view

        if status == "cancelled":
            embed.description = "This match was cancelled."
            embed.colour = discord.Colour.dark_grey()
            return embed, None

        team_a = [p for p in players if p["team"] == "A"]
        team_b = [p for p in players if p["team"] == "B"]
        done = status == "complete"
        for key, team in (("A", team_a), ("B", team_b)):
            avg = sum((p["rating_before"] if done else ratings[p["user_id"]].rating) for p in team) / len(team)
            title = f"{game.side(key)} (avg {fmt_rating(avg)})"
            if done and m["winner"] == key:
                title += " - Winner"
            embed.add_field(name=title, value="\n".join(line(p, delta=done) for p in team), inline=True)

        if status == "voting":
            vote = self.votes.get(match_id)
            if vote is None:
                embed.description = "The map vote was interrupted. Staff can cancel the match."
                return embed, match_view(match_id, ["cancel"])
            ends = discord.utils.format_dt(datetime.fromtimestamp(vote.ends_at, tz=timezone.utc), "R")
            embed.description = f"**Map vote, closes {ends}.** Everyone in the match gets a vote."
            embed.set_footer(text="Each captain can reroll the three maps once.")
            view = discord.ui.View(timeout=None)
            for i, name in enumerate(vote.options):
                view.add_item(MapVoteButton(match_id, i, f"{name} ({vote.count(name)})"))
            view.add_item(MatchButton("reroll", match_id))
            view.add_item(MatchButton("cancel", match_id))
            return embed, view

        if m["map"]:
            embed.add_field(name="Map", value=m["map"], inline=False)

        if done:
            score = f" {m['score_a']}-{m['score_b']}" if m["score_a"] is not None else ""
            embed.description = f"**{game.side(m['winner'])} won{score}.**"
            embed.colour = discord.Colour.green()
            embed.set_footer(text=f"Captains can add each team's {game.score_label} to refine ratings.")
            return embed, match_view(match_id, ["sca", "scb"], game)

        prob = win_probability([ratings[p["user_id"]] for p in team_a], [ratings[p["user_id"]] for p in team_b])
        embed.description = f"Predicted: {game.side('A')} {prob:.0%} / {game.side('B')} {1 - prob:.0%}"
        importable = game.key == "valorant" and self.bot.henrik.enabled
        embed.set_footer(
            text="When the game ends, a captain presses Game finished and the result is fetched from Riot."
            if importable
            else "Staff or a captain reports the winner when the game ends."
        )
        actions = (["done"] if importable else []) + ["wina", "winb", "cancel"]
        return embed, match_view(match_id, actions, game)

    async def refresh_match_message(self, match_id: int) -> None:
        m = await self.db.get_match(match_id)
        if not m or not m["message_id"]:
            return
        channel = self.bot.get_channel(m["channel_id"])
        if not isinstance(channel, discord.abc.Messageable):
            return
        embed, view = await self.render_match(match_id)
        try:
            await channel.get_partial_message(m["message_id"]).edit(embed=embed, view=view)
        except discord.HTTPException as e:
            log.warning("Couldn't update message for match %s: %s", match_id, e)

    async def respond_with_match(self, interaction: discord.Interaction | None, match_id: int) -> None:
        m = await self.db.get_match(match_id)
        on_message = (
            interaction is not None
            and interaction.type == discord.InteractionType.component
            and interaction.message is not None
            and interaction.message.id == m["message_id"]
        )
        if on_message and not interaction.response.is_done():
            embed, view = await self.render_match(match_id)
            await interaction.response.edit_message(embed=embed, view=view)
            return
        # Discord shows "This interaction failed" for any click that goes unanswered.
        if interaction is not None and not interaction.response.is_done():
            await interaction.response.defer()
        await self.refresh_match_message(match_id)

    async def post_match(self, channel: discord.abc.Messageable, match_id: int) -> None:
        players = await self.db.match_players(match_id)
        embed, view = await self.render_match(match_id)
        message = await channel.send(
            content="Match ready! " + " ".join(f"{mention(p['user_id'])}" for p in players), embed=embed, view=view
        )
        await self.db.update_match(match_id, channel_id=message.channel.id, message_id=message.id)
        # Warm the name cache so the draft picker renders inside the interaction deadline.
        if message.guild:
            for p in players:
                await self.name(message.guild, p["user_id"])

    async def teams_ready(self, interaction: discord.Interaction | None, match_id: int) -> None:
        """Teams are set: flip for sides, then vote on the map (or go straight live if there's no pool)."""
        m = await self.db.get_match(match_id)
        game = GAMES[m["game"]]
        if random.random() < 0.5:
            await self.db.swap_sides(match_id)
        if len(game.maps) < 2:
            await self.go_live(interaction, match_id)
            return
        self.votes[match_id] = MapVote(self.map_options(game), time.time() + MAP_VOTE_SECONDS)
        await self.db.update_match(match_id, status="voting")
        await self.respond_with_match(interaction, match_id)
        task = asyncio.create_task(self.close_vote_later(match_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    def map_options(game: Game, exclude: list[str] | None = None) -> list[str]:
        pool = [m for m in game.maps if m not in (exclude or [])]
        if len(pool) < MAP_VOTE_OPTIONS:
            pool = list(game.maps)
        return random.sample(pool, min(MAP_VOTE_OPTIONS, len(pool)))

    async def close_vote_later(self, match_id: int) -> None:
        # A reroll pushes ends_at back, so keep sleeping until the current deadline.
        while (vote := self.votes.get(match_id)) and time.time() < vote.ends_at:
            await asyncio.sleep(vote.ends_at - time.time())
        await self.finish_vote(match_id)

    async def finish_vote(self, match_id: int) -> None:
        async with self.locks[("match", match_id)]:
            vote = self.votes.pop(match_id, None)
            if vote is None:
                return
            m = await self.db.get_match(match_id)
            if not m or m["status"] != "voting":
                return
            await self.db.update_match(match_id, map=vote.winner())
            await self.go_live(None, match_id)

    async def reroll_maps(self, interaction: discord.Interaction, match_id: int) -> None:
        async with self.locks[("match", match_id)]:
            vote = self.votes.get(match_id)
            if vote is None:
                await reply(interaction, "The map vote has already closed.")
                return
            captains = {p["user_id"] for p in await self.db.match_players(match_id) if p["is_captain"]}
            if interaction.user.id not in captains:
                await reply(interaction, "Only the captains can reroll the maps.")
                return
            if interaction.user.id in vote.rerolled_by:
                await reply(interaction, "You've already used your reroll.")
                return
            m = await self.db.get_match(match_id)
            vote.rerolled_by.add(interaction.user.id)
            vote.options = self.map_options(GAMES[m["game"]], exclude=vote.options)
            vote.votes.clear()
            vote.ends_at = time.time() + MAP_VOTE_SECONDS
        await self.respond_with_match(interaction, match_id)

    async def handle_vote(self, interaction: discord.Interaction, match_id: int, index: int) -> None:
        vote = self.votes.get(match_id)
        if vote is None:
            await reply(interaction, "The map vote has already closed.")
            return
        if not any(p["user_id"] == interaction.user.id for p in await self.db.match_players(match_id)):
            await reply(interaction, "Only players in this match can vote on the map.")
            return
        vote.votes[interaction.user.id] = vote.options[index]
        await self.respond_with_match(interaction, match_id)

    # match flow

    async def handle_match_button(self, interaction: discord.Interaction, match_id: int, action: str) -> None:
        if action in ("wina", "winb"):
            if not await self.can_manage_match(interaction, match_id, captains_only=True):
                await reply(interaction, "Only staff or a captain can report the result.")
                return
            m = await self.db.get_match(match_id)
            if m["status"] != "live":
                await reply(interaction, not_live(match_id, m))
                return
            await interaction.response.send_modal(
                ResultModal(self, match_id, "A" if action == "wina" else "B", GAMES[m["game"]])
            )
            return

        if action in ("sca", "scb"):
            if not await self.can_manage_match(interaction, match_id, captains_only=True):
                await reply(interaction, "Only staff or a captain can enter the scoreboard.")
                return
            m = await self.db.get_match(match_id)
            team = "A" if action == "sca" else "B"
            assert interaction.guild is not None
            players = [
                (p["user_id"], await self.name(interaction.guild, p["user_id"]), p["score"])
                for p in await self.db.match_players(match_id)
                if p["team"] == team
            ]
            game = GAMES[m["game"]]
            await interaction.response.send_modal(
                ScoreboardModal(self, match_id, game.side(team), players, game.score_label)
            )
            return

        if action == "done":
            if not await self.can_manage_match(interaction, match_id, captains_only=True):
                await reply(interaction, "Only staff or a captain can say the game is finished.")
                return
            await self.bot.get_cog("Importer").start(interaction, match_id)
            return

        if action == "cancel":
            if not await is_staff(interaction):
                await reply(interaction, "Only staff can cancel a match.")
                return
            await self.cancel_match(interaction, match_id)
            return

        if action == "reroll":
            await self.reroll_maps(interaction, match_id)
            return

        if not await self.can_manage_match(interaction, match_id, captains_only=False):
            await reply(interaction, "Only players in this match can choose how teams are picked.")
            return
        async with self.locks[("match", match_id)]:
            m = await self.db.get_match(match_id)
            if m["status"] != "setup":
                await reply(interaction, "Teams are already being picked.")
                return
            if action == "auto":
                await self.start_auto(interaction, match_id)
            else:
                await self.start_draft(interaction, match_id, "random" if action == "draftr" else "top")

    @staticmethod
    def random_map(game: Game, exclude: str | None = None) -> str | None:
        pool = [m for m in game.maps if m != exclude] or list(game.maps)
        return random.choice(pool) if pool else None

    async def start_auto(self, interaction: discord.Interaction | None, match_id: int) -> None:
        m = await self.db.get_match(match_id)
        players = await self.db.match_players(match_id)
        ratings = await self.ratings([p["user_id"] for p in players], m["game"])
        team_a, team_b = auto_balance(list(ratings.values()))
        captains = (max(team_a, key=lambda p: p.rating).user_id, max(team_b, key=lambda p: p.rating).user_id)
        await self.db.set_teams(match_id, [p.user_id for p in team_a], [p.user_id for p in team_b], captains)
        await self.db.update_match(match_id, mode="auto")
        await self.teams_ready(interaction, match_id)

    async def start_draft(self, interaction: discord.Interaction | None, match_id: int, method: str) -> None:
        m = await self.db.get_match(match_id)
        players = await self.db.match_players(match_id)
        ratings = await self.ratings([p["user_id"] for p in players], m["game"])
        first, second = choose_captains(list(ratings.values()), method)
        pool = [u for u in ratings if u not in (first.user_id, second.user_id)]
        self.drafts[match_id] = DraftState(
            captains=(first.user_id, second.user_id),
            order=snake_order(len(pool)),
            teams=([first.user_id], [second.user_id]),
            pool=pool,
        )
        await self.db.set_teams(match_id, [first.user_id], [second.user_id], (first.user_id, second.user_id))
        await self.db.update_match(match_id, status="drafting", mode=f"draft-{method}")
        await self.respond_with_match(interaction, match_id)
        task = asyncio.create_task(self.dm_captains(match_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def pool_embed(self, match_id: int, captain: int) -> discord.Embed:
        """Scouting list sent privately to a captain at the start of a draft."""
        m = await self.db.get_match(match_id)
        game = GAMES[m["game"]]
        state = self.drafts[match_id]
        guild = self.bot.get_guild(m["guild_id"])
        side = state.captains.index(captain)
        opponent = state.captains[1 - side]

        async def describe(uid: int) -> str:
            r = await self.db.get_or_create_rating(uid, game.key, DEFAULT_RATING)
            name = await self.name(guild, uid) if guild else str(uid)
            parts = [f"**{name}**", f"{fmt_rating(r.rating)} Elo"]
            approx = game.rank_for_rating(r.rating)
            if approx:
                parts.append(f"~{approx}")
            parts.append(f"{r.wins}W {r.losses}L" + (f" ({r.wins / r.games:.0%})" if r.games else ", no games yet"))
            player = await self.db.get_player(uid)
            if game.key == "valorant" and player and player["riot_id"]:
                parts.append(f"Riot ID {player['riot_id']}")
            return " · ".join(parts)

        ratings = await self.ratings(state.pool, game.key)
        pool = sorted(state.pool, key=lambda u: ratings[u].rating, reverse=True)
        lines = [await describe(u) for u in pool]
        turns = [i + 1 for i, s in enumerate(state.order) if s == side]
        link = f"https://discord.com/channels/{m['guild_id']}/{m['channel_id']}/{m['message_id']}"
        embed = discord.Embed(
            title=f"Match #{match_id} draft: you're a captain",
            description=(
                f"You pick **{'first' if side == 0 else 'second'}**, on picks {', '.join(map(str, turns))} of {len(state.order)}.\n"
                f"Opposing captain: {await describe(opponent)}\n\n"
                f"Make your picks on the [match message]({link})."
            ),
            colour=COLOUR,
        )
        embed.add_field(name=f"Player pool ({len(pool)})", value="\n".join(lines)[:1024], inline=False)
        return embed

    async def dm_captains(self, match_id: int) -> None:
        state = self.drafts.get(match_id)
        if state is None:
            return
        m = await self.db.get_match(match_id)
        failed = []
        for captain in state.captains:
            if captain in FAKE_PLAYERS:
                continue
            try:
                user = self.bot.get_user(captain) or await self.bot.fetch_user(captain)
                await user.send(embed=await self.pool_embed(match_id, captain))
            except discord.HTTPException:
                failed.append(captain)
        channel = self.bot.get_channel(m["channel_id"])
        if failed and isinstance(channel, discord.abc.Messageable):
            await channel.send(
                " ".join(f"{mention(u)}" for u in failed)
                + " I couldn't DM you the player pool. Allow DMs from server members to get it next time."
            )

    async def handle_pick(self, interaction: discord.Interaction, match_id: int, picked: int) -> None:
        async with self.locks[("match", match_id)]:
            state = self.drafts.get(match_id)
            if state is None:
                await reply(interaction, "This draft isn't running any more.")
                return
            if interaction.user.id != state.current_captain and not await is_staff(interaction):
                await reply(interaction, f"It's {mention(state.current_captain)}'s pick.")
                return
            if picked not in state.pool:
                await reply(interaction, "That player has already been picked.")
                return
            await self.pick(interaction, match_id, picked)

    async def pick(self, interaction: discord.Interaction | None, match_id: int, picked: int) -> None:
        """Applies a validated pick. The caller holds the match lock."""
        state = self.drafts[match_id]
        state.pick(picked)
        if not state.done:
            await self.respond_with_match(interaction, match_id)
            return
        del self.drafts[match_id]
        await self.db.set_teams(match_id, state.teams[0], state.teams[1], state.captains, state.pick_log)
        await self.teams_ready(interaction, match_id)

    async def go_live(self, interaction: discord.Interaction | None, match_id: int) -> None:
        m = await self.db.get_match(match_id)
        await self.db.update_match(
            match_id, status="live", map=m["map"] or self.random_map(GAMES[m["game"]]), live_at=int(time.time())
        )
        await self.respond_with_match(interaction, match_id)
        await self.announce_teams(match_id)

    async def announce_teams(self, match_id: int) -> None:
        m = await self.db.get_match(match_id)
        channel = self.bot.get_channel(m["channel_id"])
        if not isinstance(channel, discord.abc.Messageable):
            return
        game = GAMES[m["game"]]
        players = await self.db.match_players(match_id)
        lines = [f"**Match #{match_id} is live**" + (f" on **{m['map']}**" if m["map"] else "")]
        for key in ("A", "B"):
            team = [p for p in players if p["team"] == key]
            team.sort(key=lambda p: not p["is_captain"])
            mentions = " ".join(f"{mention(p['user_id'])}" + (" (C)" if p["is_captain"] else "") for p in team)
            lines.append(f"**{game.side(key)}:** {mentions}")
        try:
            await channel.send(
                "\n".join(lines),
                reference=channel.get_partial_message(m["message_id"]) if m["message_id"] else None,
                allowed_mentions=discord.AllowedMentions(users=True, replied_user=False),
            )
        except discord.HTTPException:
            log.warning("Couldn't announce teams for match %s", match_id)

    async def finish_match(
        self, interaction: discord.Interaction, match_id: int, winner: str, score: tuple[int, int] | None
    ) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        error = await self.record_result(match_id, winner, score, interaction.user.id)
        await reply(interaction, error or f"Result recorded for match #{match_id}.")

    async def record_result(
        self,
        match_id: int,
        winner: str,
        score: tuple[int, int] | None,
        reported_by: int,
        scores: dict[int, float] | None = None,
        map_name: str | None = None,
    ) -> str | None:
        """Returns an error message, or None once the result is stored and ratings refitted."""
        async with self.locks[("match", match_id)]:
            m = await self.db.get_match(match_id)
            if not m or m["status"] != "live":
                return not_live(match_id, m)
            if score and ((score[0] > score[1]) != (winner == "A")):
                return "The score doesn't match the winning team."
            teams = {p["user_id"]: p["team"] for p in await self.db.match_players(match_id)}
            if scores:
                await self.db.set_scores(match_id, scores)
            if map_name:
                await self.db.update_match(match_id, map=map_name)
            await self.db.apply_result(match_id, m["game"], winner, teams, score, reported_by)
            await self.bot.refit(m["game"], credit_match=match_id)
        await self.refresh_match_message(match_id)
        return None

    async def save_scoreboard(self, interaction: discord.Interaction, match_id: int, scores: dict[int, float]) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        error = await self.record_scoreboard(match_id, scores)
        await reply(interaction, error or "Scoreboard saved and ratings updated.")

    async def record_scoreboard(self, match_id: int, scores: dict[int, float]) -> str | None:
        async with self.locks[("match", match_id)]:
            m = await self.db.get_match(match_id)
            if not m or m["status"] != "complete":
                return "Scoreboards can only be entered for completed matches."
            await self.db.set_scores(match_id, scores)
            await self.bot.refit(m["game"], credit_match=match_id)
        await self.refresh_match_message(match_id)
        return None

    async def cancel_match(self, interaction: discord.Interaction, match_id: int) -> None:
        async with self.locks[("match", match_id)]:
            m = await self.db.get_match(match_id)
            if not m or m["status"] not in ("setup", "drafting", "voting", "live"):
                await reply(interaction, "That match can't be cancelled.")
                return
            self.drafts.pop(match_id, None)
            self.votes.pop(match_id, None)
            await self.db.update_match(match_id, status="cancelled")
        await self.respond_with_match(interaction, match_id)

    # slash commands

    @match.command(name="report", description="Report the winner of a match")
    @app_commands.describe(
        winning_player="Anyone on the winning team",
        winner_rounds="Rounds won by the winning team",
        loser_rounds="Rounds won by the losing team",
    )
    async def match_report(
        self,
        interaction: discord.Interaction,
        match_id: int,
        winning_player: discord.Member,
        winner_rounds: app_commands.Range[int, 0, 99] | None = None,
        loser_rounds: app_commands.Range[int, 0, 99] | None = None,
    ) -> None:
        if not await self.can_manage_match(interaction, match_id, captains_only=True):
            await reply(interaction, "Only staff or a captain can report the result.")
            return
        team = next((p["team"] for p in await self.db.match_players(match_id) if p["user_id"] == winning_player.id), None)
        if team is None:
            await reply(interaction, f"{winning_player.mention} isn't on a team in match #{match_id}.")
            return
        score = None
        if winner_rounds is not None and loser_rounds is not None:
            score = (winner_rounds, loser_rounds) if team == "A" else (loser_rounds, winner_rounds)
        await self.finish_match(interaction, match_id, team, score)

    @match.command(name="import", description="Fetch a live match's result from Riot's match history")
    async def match_import(self, interaction: discord.Interaction, match_id: int) -> None:
        if not await self.can_manage_match(interaction, match_id, captains_only=True):
            await reply(interaction, "Only staff or a captain can fetch the result.")
            return
        await self.bot.get_cog("Importer").start(interaction, match_id)

    @match.command(name="cancel", description="Cancel a match without affecting ratings")
    @staff_only()
    async def match_cancel(self, interaction: discord.Interaction, match_id: int) -> None:
        await self.cancel_match(interaction, match_id)
        if not interaction.response.is_done():
            await reply(interaction, f"Match #{match_id} cancelled.")

    @match.command(name="undo", description="Undo a reported result and reverse the Elo changes")
    @staff_only()
    async def match_undo(self, interaction: discord.Interaction, match_id: int) -> None:
        async with self.locks[("match", match_id)]:
            m = await self.db.get_match(match_id)
            if not m or m["status"] != "complete":
                await reply(interaction, "Only completed matches can be undone.")
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self.db.revert_result(match_id, m["game"])
            await self.bot.refit(m["game"])
        await self.refresh_match_message(match_id)
        await reply(interaction, f"Result for match #{match_id} undone. Report it again with the buttons or `/match report`.")

    @match.command(name="sub", description="Swap a player in a match for a substitute")
    @staff_only()
    async def match_sub(
        self, interaction: discord.Interaction, match_id: int, out: discord.Member, sub: discord.Member
    ) -> None:
        async with self.locks[("match", match_id)]:
            m = await self.db.get_match(match_id)
            if not m or m["status"] not in ("setup", "voting", "live"):
                await reply(interaction, "Subs can only be made before teams are picked or during a live match.")
                return
            teams = {p["user_id"]: p["team"] for p in await self.db.match_players(match_id)}
            ids = set(teams)
            if out.id not in ids:
                await reply(interaction, f"{out.mention} isn't in match #{match_id}.")
                return
            if sub.id in ids or await self.db.active_match_for(sub.id):
                await reply(interaction, f"{sub.mention} is already in a match.")
                return
            await self.db.get_or_create_rating(sub.id, m["game"], DEFAULT_RATING)
            await self.db.db.execute(
                "UPDATE match_players SET user_id = ? WHERE match_id = ? AND user_id = ?", (sub.id, match_id, out.id)
            )
            await self.db.db.commit()
            await self.db.leave_queue(m["guild_id"], m["game"], sub.id)
        await self.refresh_match_message(match_id)
        team = f" on **{GAMES[m['game']].side(teams[out.id])}**" if teams[out.id] else ""
        await reply(interaction, f"{sub.mention} is in for {out.mention} in match #{match_id}{team}.", ephemeral=False)


async def setup(bot: TenManBot) -> None:
    await bot.add_cog(Lobby(bot))
