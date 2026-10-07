from alembic import context

from src.database.models import Base


def run_migrations():
    # The caller supplies a connection: passwords never enter alembic.ini or logs.
    connection = context.config.attributes["connection"]
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


run_migrations()
