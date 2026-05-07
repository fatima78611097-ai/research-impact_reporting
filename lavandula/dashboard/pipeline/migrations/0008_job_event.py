from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('pipeline', '0007_job_lifecycle_v2'),
    ]

    operations = [
        migrations.CreateModel(
            name='JobEvent',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('event_type', models.CharField(
                    choices=[
                        ('created', 'Created'),
                        ('blocked', 'Blocked'),
                        ('scheduled', 'Scheduled'),
                        ('started', 'Started'),
                        ('progress', 'Progress'),
                        ('warning', 'Warning'),
                        ('error', 'Error'),
                        ('completed', 'Completed'),
                        ('failed', 'Failed'),
                        ('cancelled', 'Cancelled'),
                        ('retried', 'Retried'),
                        ('reset', 'Reset'),
                        ('dependency_rebound', 'Dependency Rebound'),
                    ],
                    db_index=True,
                    max_length=20,
                )),
                ('payload', models.JSONField(default=dict)),
                ('job', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='events',
                    to='pipeline.job',
                )),
            ],
            options={
                'ordering': ['timestamp'],
                'indexes': [
                    models.Index(fields=['job', 'timestamp'], name='pipeline_jo_job_id_timest_idx'),
                    models.Index(fields=['event_type', 'timestamp'], name='pipeline_jo_event_t_timest_idx'),
                ],
                'db_table': 'job_events',
            },
        ),
    ]
