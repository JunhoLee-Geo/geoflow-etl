"""Migration connection settings come only from process environment."""

import os

from alembic import context
from sqlalchemy import create_engine, pool

from geoflow.database.models import Base
from geoflow.database.repository import normalize_url

url = normalize_url(os.environ.get("GEOFLOW_DATABASE_URL", ""))
if context.is_offline_mode():
    context.configure(
        url=url, target_metadata=Base.metadata, literal_binds=True, include_schemas=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=Base.metadata, include_schemas=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
