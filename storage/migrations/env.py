"""Configuración de Alembic: usa la misma URL (`storage.db.database_url`) y
las mismas tablas (`storage.tables`) que el resto del proyecto."""

import logging
from logging.config import fileConfig

from alembic import context

from storage.db import _hide_password, database_url, make_engine
from storage.tables import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
logger = logging.getLogger("alembic.env")

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Al comparar BD contra `tables.py` (autogenerate / `alembic check`),
    ignorar las FK a `auth.users`: solo existen en Supabase y las pone la
    migración 0001, no `tables.py`."""
    if type_ == "foreign_key_constraint" and obj.referred_table.schema == "auth":
        return False
    return True


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: solo imprime el SQL, sin conectarse."""
    url = database_url()
    logger.info("generando SQL para %s", _hide_password(url))
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine()
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
