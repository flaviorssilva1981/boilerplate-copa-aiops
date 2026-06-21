import asyncio
import os
import sys

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


async def main():
    report_id = sys.argv[1] if len(sys.argv) > 1 else None
    if not report_id:
        raise SystemExit("usage: read_report.py <report-uuid>")

    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with AsyncSession(engine) as s:
        r = await s.execute(
            sa.text("SELECT markdown, status FROM reports WHERE id=cast(:id as uuid)"),
            {"id": report_id},
        )
        row = r.one()
        print("STATUS:", row.status)
        print("--- LAST 4000 CHARS ---")
        print((row.markdown or "empty")[-4000:])


asyncio.run(main())
