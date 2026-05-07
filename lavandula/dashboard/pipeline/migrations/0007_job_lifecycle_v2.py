from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('pipeline', '0006_add_job_log_tail'),
    ]

    operations = [
        migrations.AlterField(
            model_name='job',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('scheduled', 'Scheduled'),
                    ('running', 'Running'),
                    ('completed', 'Completed'),
                    ('failed', 'Failed'),
                    ('cancelled', 'Cancelled'),
                ],
                default='pending',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='job',
            name='blocked_reason',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='job',
            name='retry_of',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='retries',
                to='pipeline.job',
            ),
        ),
        migrations.AddField(
            model_name='job',
            name='attempt_number',
            field=models.IntegerField(default=1),
        ),
        migrations.AddField(
            model_name='job',
            name='started_at_precise',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='worker',
            name='cpu_pct',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='worker',
            name='mem_pct',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='worker',
            name='has_gpu',
            field=models.BooleanField(default=False),
        ),
    ]
