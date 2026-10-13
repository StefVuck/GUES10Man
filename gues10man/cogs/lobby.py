from __future__ import annotations

import asyncio
import logging
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..balance import auto_balance, choose_captains, snake_order
from ..elo import PlayerRating, win_probability
from ..games import DEFAULT_RATING, GAMES, Game
from ..util import GAME_CHOICES, TEAM_NAMES, fmt_delta, fmt_rating, is_staff, reply, staff_only

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
    "wina": (f"{TEAM_NAMES['A']} won", discord.ButtonStyle.success),
    "winb": (f"{TEAM_NAMES['B']} won", discord.ButtonStyle.success),
    "map": ("Reroll map", discord.ButtonStyle.secondary),
    "sca": (f"{TEAM_NAMES['A']} scoreboard", discord.ButtonStyle.secondary),
    "scb": (f"{TEAM_NAMES['B']} scoreboard", discord.ButtonStyle.secondary),
    "cancel": ("Cancel match", discord.ButtonStyle.danger),
}


class MatchButton(discord.ui.DynamicItem[discord.ui.Button], template=r"m:(?P<action>[a-z]+):(?P<id>\d+)"):
    def __init__(self, action: str, match_id: int):
        label, style = MATCH_BUTTONS[action]
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"m:{action}:{match_id}"))
        self.action = action
        self.match_id = match_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        if match["action"] not in MATCH_BUTTONS:
            raise ValueError(match["action"])
        return cls(match["action"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await lobby(interaction).handle_match_button(interaction, self.match_id, self.action)


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
    score_a = discord.ui.TextInput(label=f"{TEAM_NAMES['A']} rounds (optional)", required=False, max_length=2)
    score_b = discord.ui.TextInput(label=f"{TEAM_NAMES['B']} rounds (optional)", required=False, max_length=2)

    def __init__(self, cog: Lobby, match_id: int, winner: str):
        super().__init__(title=f"Report match #{match_id}: {TEAM_NAMES[winner]} won")
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

    def __init__(self, cog: Lobby, match_id: int, team: str, players: list[tuple[int, str, float | None]], label: str):
        super().__init__(title=f"Match #{match_id}: {TEAM_NAMES[team]} {label}")
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


def queue_view(game: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(QueueButton("join", game))
    view.add_item(QueueButton("leave", game))
    return view


def match_view(match_id: int, actions: list[str]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in actions:
        view.add_item(MatchButton(action, match_id))
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

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(QueueButton, MatchButton, DraftSelect)

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

    # helpers

    async def name(self, guild: discord.Guild, user_id: int) -> str:
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
            f"`{i:>2}` <@{uid}> ({fmt_rating(ratings[uid].rating)})" for i, uid in enumerate(members, 1)
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
            text = f"<@{uid}> ({fmt_rating(rating)})"
            if p["is_captain"]:
                text = "👑 " + text
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
            for side, key in ((0, "A"), (1, "B")):
                members = [next(p for p in players if p["user_id"] == u) for u in state.teams[side]]
                embed.add_field(name=TEAM_NAMES[key], value="\n".join(line(p) for p in members) or "-", inline=True)
            embed.add_field(
                name="Available",
                value="\n".join(f"<@{u}> ({fmt_rating(ratings[u].rating)})" for u in state.pool) or "-",
                inline=False,
            )
            embed.description = f"<@{state.current_captain}> is picking (pick {state.pick_number + 1} of {len(state.order)})."
            guild = self.bot.get_guild(m["guild_id"])
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
            title = f"{TEAM_NAMES[key]} (avg {fmt_rating(avg)})"
            if done and m["winner"] == key:
                title = "🏆 " + title
            embed.add_field(name=title, value="\n".join(line(p, delta=done) for p in team), inline=True)

        if m["map"]:
            embed.add_field(name="Map", value=m["map"], inline=False)

        if done:
            score = f" {m['score_a']}-{m['score_b']}" if m["score_a"] is not None else ""
            embed.description = f"**{TEAM_NAMES[m['winner']]} won{score}.**"
            embed.colour = discord.Colour.green()
            embed.set_footer(text=f"Captains can add each team's {game.score_label} to refine ratings.")
            return embed, match_view(match_id, ["sca", "scb"])

        prob = win_probability([ratings[p["user_id"]] for p in team_a], [ratings[p["user_id"]] for p in team_b])
        embed.description = f"Predicted: {TEAM_NAMES['A']} {prob:.0%} / {TEAM_NAMES['B']} {1 - prob:.0%}"
        embed.set_footer(text="Staff or a captain reports the winner when the game ends.")
        actions = ["wina", "winb"] + (["map"] if game.maps else []) + ["cancel"]
        return embed, match_view(match_id, actions)

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
        except discord.HTTPException:
            log.warning("Couldn't update message for match %s", match_id)

    async def respond_with_match(self, interaction: discord.Interaction, match_id: int) -> None:
        m = await self.db.get_match(match_id)
        on_message = (
            interaction.type == discord.InteractionType.component
            and interaction.message is not None
            and interaction.message.id == m["message_id"]
        )
        if on_message and not interaction.response.is_done():
            embed, view = await self.render_match(match_id)
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            await self.refresh_match_message(match_id)

    async def post_match(self, channel: discord.abc.Messageable, match_id: int) -> None:
        players = await self.db.match_players(match_id)
        embed, view = await self.render_match(match_id)
        message = await channel.send(
            content="Match ready! " + " ".join(f"<@{p['user_id']}>" for p in players), embed=embed, view=view
        )
        await self.db.update_match(match_id, channel_id=message.channel.id, message_id=message.id)
        # Warm the name cache so the draft picker renders inside the interaction deadline.
        if message.guild:
            for p in players:
                await self.name(message.guild, p["user_id"])

    # match flow

    async def handle_match_button(self, interaction: discord.Interaction, match_id: int, action: str) -> None:
        if action in ("wina", "winb"):
            if not await self.can_manage_match(interaction, match_id, captains_only=True):
                await reply(interaction, "Only staff or a captain can report the result.")
                return
            m = await self.db.get_match(match_id)
            if m["status"] != "live":
                await reply(interaction, "This match isn't in progress.")
                return
            await interaction.response.send_modal(ResultModal(self, match_id, "A" if action == "wina" else "B"))
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
            label = GAMES[m["game"]].score_label
            await interaction.response.send_modal(ScoreboardModal(self, match_id, team, players, label))
            return

        if action == "cancel":
            if not await is_staff(interaction):
                await reply(interaction, "Only staff can cancel a match.")
                return
            await self.cancel_match(interaction, match_id)
            return

        if action == "map":
            if not await self.can_manage_match(interaction, match_id, captains_only=True):
                await reply(interaction, "Only staff or a captain can reroll the map.")
                return
            m = await self.db.get_match(match_id)
            if m["status"] != "live":
                await reply(interaction, "This match isn't in progress.")
                return
            await self.db.update_match(match_id, map=self.random_map(GAMES[m["game"]], exclude=m["map"]))
            await self.respond_with_match(interaction, match_id)
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

    async def start_auto(self, interaction: discord.Interaction, match_id: int) -> None:
        m = await self.db.get_match(match_id)
        players = await self.db.match_players(match_id)
        ratings = await self.ratings([p["user_id"] for p in players], m["game"])
        team_a, team_b = auto_balance(list(ratings.values()))
        captains = (max(team_a, key=lambda p: p.rating).user_id, max(team_b, key=lambda p: p.rating).user_id)
        await self.db.set_teams(match_id, [p.user_id for p in team_a], [p.user_id for p in team_b], captains)
        await self.db.update_match(match_id, mode="auto")
        await self.go_live(interaction, match_id)

    async def start_draft(self, interaction: discord.Interaction, match_id: int, method: str) -> None:
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

    async def handle_pick(self, interaction: discord.Interaction, match_id: int, picked: int) -> None:
        async with self.locks[("match", match_id)]:
            state = self.drafts.get(match_id)
            if state is None:
                await reply(interaction, "This draft isn't running any more.")
                return
            if interaction.user.id != state.current_captain and not await is_staff(interaction):
                await reply(interaction, f"It's <@{state.current_captain}>'s pick.")
                return
            if picked not in state.pool:
                await reply(interaction, "That player has already been picked.")
                return
            state.pick(picked)
            if not state.done:
                await self.respond_with_match(interaction, match_id)
                return
            del self.drafts[match_id]
            await self.db.set_teams(match_id, state.teams[0], state.teams[1], state.captains, state.pick_log)
            await self.go_live(interaction, match_id)

    async def go_live(self, interaction: discord.Interaction, match_id: int) -> None:
        m = await self.db.get_match(match_id)
        await self.db.update_match(match_id, status="live", map=self.random_map(GAMES[m["game"]]))
        await self.respond_with_match(interaction, match_id)
        if interaction.guild:
            await self.move_to_team_channels(interaction.guild, match_id)

    async def finish_match(
        self, interaction: discord.Interaction, match_id: int, winner: str, score: tuple[int, int] | None
    ) -> None:
        async with self.locks[("match", match_id)]:
            m = await self.db.get_match(match_id)
            if not m or m["status"] != "live":
                await reply(interaction, "This match isn't in progress, so there's nothing to report.")
                return
            if score and ((score[0] > score[1]) != (winner == "A")):
                await reply(interaction, "The score doesn't match the winning team.")
                return
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
            teams = {p["user_id"]: p["team"] for p in await self.db.match_players(match_id)}
            await self.db.apply_result(match_id, m["game"], winner, teams, score, interaction.user.id)
            await self.bot.refit(m["game"], credit_match=match_id)

        await self.refresh_match_message(match_id)
        await reply(interaction, f"Result recorded for match #{match_id}.", ephemeral=True)
        if interaction.guild:
            await self.move_to_lobby(interaction.guild, match_id)

    async def save_scoreboard(self, interaction: discord.Interaction, match_id: int, scores: dict[int, float]) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.locks[("match", match_id)]:
            m = await self.db.get_match(match_id)
            if not m or m["status"] != "complete":
                await reply(interaction, "Scoreboards can only be entered for completed matches.")
                return
            await self.db.set_scores(match_id, scores)
            await self.bot.refit(m["game"], credit_match=match_id)
        await self.refresh_match_message(match_id)
        await reply(interaction, "Scoreboard saved and ratings updated.")

    async def cancel_match(self, interaction: discord.Interaction, match_id: int) -> None:
        async with self.locks[("match", match_id)]:
            m = await self.db.get_match(match_id)
            if not m or m["status"] not in ("setup", "drafting", "live"):
                await reply(interaction, "That match can't be cancelled.")
                return
            was_live = m["status"] == "live"
            self.drafts.pop(match_id, None)
            await self.db.update_match(match_id, status="cancelled")
        await self.respond_with_match(interaction, match_id)
        if was_live and interaction.guild:
            await self.move_to_lobby(interaction.guild, match_id)

    # voice

    async def move_to_team_channels(self, guild: discord.Guild, match_id: int) -> None:
        config = await self.db.get_config(guild.id)
        channels = {"A": guild.get_channel(config.team_a_vc_id or 0), "B": guild.get_channel(config.team_b_vc_id or 0)}
        if not all(isinstance(c, discord.VoiceChannel) for c in channels.values()):
            return
        for p in await self.db.match_players(match_id):
            member = guild.get_member(p["user_id"])
            if member and member.voice and member.voice.channel:
                try:
                    await member.move_to(channels[p["team"]], reason=f"10 man match #{match_id}")
                except discord.HTTPException:
                    log.warning("Couldn't move %s", member)

    async def move_to_lobby(self, guild: discord.Guild, match_id: int) -> None:
        config = await self.db.get_config(guild.id)
        lobby_vc = guild.get_channel(config.lobby_vc_id or 0)
        if not isinstance(lobby_vc, discord.VoiceChannel):
            return
        team_ids = {config.team_a_vc_id, config.team_b_vc_id}
        for p in await self.db.match_players(match_id):
            member = guild.get_member(p["user_id"])
            if member and member.voice and member.voice.channel and member.voice.channel.id in team_ids:
                try:
                    await member.move_to(lobby_vc, reason=f"10 man match #{match_id} finished")
                except discord.HTTPException:
                    log.warning("Couldn't move %s", member)

    # slash commands

    @match.command(name="report", description="Report the winner of a match")
    @app_commands.describe(winner="The team that won", score_a="Rounds won by Team A", score_b="Rounds won by Team B")
    @app_commands.choices(winner=[app_commands.Choice(name=v, value=k) for k, v in TEAM_NAMES.items()])
    async def match_report(
        self,
        interaction: discord.Interaction,
        match_id: int,
        winner: str,
        score_a: app_commands.Range[int, 0, 99] | None = None,
        score_b: app_commands.Range[int, 0, 99] | None = None,
    ) -> None:
        if not await self.can_manage_match(interaction, match_id, captains_only=True):
            await reply(interaction, "Only staff or a captain can report the result.")
            return
        score = (score_a, score_b) if score_a is not None and score_b is not None else None
        await self.finish_match(interaction, match_id, winner, score)

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
            if not m or m["status"] not in ("setup", "live"):
                await reply(interaction, "Subs can only be made before teams are picked or during a live match.")
                return
            ids = {p["user_id"] for p in await self.db.match_players(match_id)}
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
        await reply(interaction, f"{sub.mention} is in for {out.mention} in match #{match_id}.", ephemeral=False)


async def setup(bot: TenManBot) -> None:
    await bot.add_cog(Lobby(bot))
