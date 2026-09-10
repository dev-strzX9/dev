from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


async def configure_connection(conn):
    await conn.execute("SET TIME ZONE 'UTC'")
    await conn.commit()


def make_pool(database_url: str):
    return AsyncConnectionPool(database_url, min_size=2, max_size=10, open=False,
                               kwargs={"row_factory": dict_row}, configure=configure_connection)


async def one(conn, sql, params=()):
    return await (await conn.execute(sql, params)).fetchone()


async def all_rows(conn, sql, params=()):
    return await (await conn.execute(sql, params)).fetchall()
