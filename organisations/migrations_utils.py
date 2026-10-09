"""Migration helpers for the `updated_seq` sync-cursor trigger.

docs/04-design.md §1.1: a single PostgreSQL sequence and trigger function are
created once (organisations' first migration), and every SyncedTenantModel
table gets a BEFORE INSERT OR UPDATE trigger that stamps `updated_seq` from
that sequence. Unlike a value set in `save()`, a DB trigger also catches
QuerySet.update().
"""

from django.db import migrations

CREATE_SEQUENCE_AND_FUNCTION_SQL = """
CREATE SEQUENCE IF NOT EXISTS workflow_updated_seq;

CREATE OR REPLACE FUNCTION workflow_set_updated_seq()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_seq := nextval('workflow_updated_seq');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

DROP_SEQUENCE_AND_FUNCTION_SQL = """
DROP FUNCTION IF EXISTS workflow_set_updated_seq();
DROP SEQUENCE IF EXISTS workflow_updated_seq;
"""


def create_seq_and_function() -> migrations.RunSQL:
    """Creates `workflow_updated_seq` and `workflow_set_updated_seq()`.

    Must be applied before any `install_seq_trigger()` call — put it first
    in organisations' initial migration, and make every other app's trigger
    migration depend on that migration.
    """
    return migrations.RunSQL(
        sql=CREATE_SEQUENCE_AND_FUNCTION_SQL,
        reverse_sql=DROP_SEQUENCE_AND_FUNCTION_SQL,
    )


def install_seq_trigger(table_name: str) -> migrations.RunSQL:
    """Returns a RunSQL operation installing the updated_seq trigger on
    `table_name` (with a reverse operation that drops it)."""
    trigger_name = f"{table_name}_set_updated_seq"
    forward = (
        f"CREATE TRIGGER {trigger_name} "
        f"BEFORE INSERT OR UPDATE ON {table_name} "
        f"FOR EACH ROW EXECUTE FUNCTION workflow_set_updated_seq();"
    )
    reverse = f"DROP TRIGGER IF EXISTS {trigger_name} ON {table_name};"
    return migrations.RunSQL(sql=forward, reverse_sql=reverse)
