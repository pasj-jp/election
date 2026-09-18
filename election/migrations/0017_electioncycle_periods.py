from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("election", "0016_paper_voting"),
    ]

    operations = [
        migrations.AddField(
            model_name="electioncycle",
            name="final_end_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="本選挙終了日時",
            ),
        ),
        migrations.AddField(
            model_name="electioncycle",
            name="final_start_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="本選挙開始日時",
            ),
        ),
        migrations.AddField(
            model_name="electioncycle",
            name="preliminary_end_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="予備選挙終了日時",
            ),
        ),
        migrations.AddField(
            model_name="electioncycle",
            name="preliminary_start_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="予備選挙開始日時",
            ),
        ),
    ]
