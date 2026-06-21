import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def main():
    engine = create_async_engine(os.environ["DATABASE_URL"])
    sm = async_sessionmaker(engine)
    async with sm() as s:
        r = await s.execute(
            text("SELECT status, COUNT(*) FROM reports GROUP BY status ORDER BY status")
        )
        for row in r.fetchall():
            print(row[0], row[1])
        r2 = await s.execute(
            text(
                "SELECT COUNT(*) FROM reports "
                "WHERE status='INCOMPLETO' AND coalesce(markdown,'')=''"
            )
        )
        print("empty_incompleto:", r2.scalar())
    await engine.dispose()


asyncio.run(main())
