from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from src.config.runtime import DSN


class Base(DeclarativeBase):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


engine = create_engine(DSN, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)


def ensure_schema():
    """create_all 负责新表；已存在的旧库只补齐本版新增的 JSON 列。"""
    inspector = inspect(engine)
    existing = {table: {column['name'] for column in inspector.get_columns(table)}
                for table in inspector.get_table_names()}
    additions = {'analysis_runs': {'correction_snapshot': 'JSON'}}
    with engine.begin() as connection:
        for table, columns in additions.items():
            if table not in existing:
                continue
            for name, kind in columns.items():
                if name not in existing[table]:
                    connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {kind}'))
