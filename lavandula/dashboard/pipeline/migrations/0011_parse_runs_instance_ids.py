"""No-op stub: documents parse_runs.instance_ids TEXT[] column.

The actual DDL lives in lavandula/migrations/parse/003_work_queue.sql
and is applied manually on RDS by the operator. This migration exists
to keep the Django migration sequence consistent.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("pipeline", "0010_add_parse_phase")]
    operations = [
        migrations.RunSQL(
            sql=migrations.RunSQL.noop,
            reverse_sql=migrations.RunSQL.noop,
            state_operations=[],
        ),
    ]
