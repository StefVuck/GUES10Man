from types import SimpleNamespace

from gues10man.config import Settings
from gues10man.util import is_staff


def interaction(user_id, owner_id=None, staff=()):
    async def is_owner(user):
        return user.id == owner_id

    settings = Settings("t", None, None, "eu", ":memory:", frozenset(staff))
    client = SimpleNamespace(settings=settings, is_owner=is_owner)
    return SimpleNamespace(user=SimpleNamespace(id=user_id), client=client)


async def test_bot_owner_is_staff():
    assert await is_staff(interaction(1, owner_id=1))


async def test_listed_user_is_staff():
    assert await is_staff(interaction(2, staff={2}))


async def test_other_users_are_not():
    assert not await is_staff(interaction(3, owner_id=1, staff={2}))
