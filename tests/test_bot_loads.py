from gues10man.bot import EXTENSIONS, TenManBot
from gues10man.config import Settings


async def test_extensions_register_commands(tmp_path):
    settings = Settings("token", None, None, "eu", str(tmp_path / "bot.db"))
    bot = TenManBot(settings)
    await bot.db.connect()
    for ext in EXTENSIONS:
        await bot.load_extension(ext)
    names = {c.name for c in bot.tree.get_commands()}
    assert {"queue", "match", "link", "setrank", "profile", "history", "leaderboard", "setup", "rating"} <= names
    for cmd in bot.tree.walk_commands():
        cmd.to_dict(bot.tree)
    await bot.db.close()
