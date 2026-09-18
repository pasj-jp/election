from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("election", "0013_alter_lotterydraw_category"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="election",
            options={
                "ordering": [
                    "cycle",
                    "phase",
                    "office",
                    "representative_category",
                ],
                "verbose_name": "選挙",
                "verbose_name_plural": "選挙",
            },
        ),
        migrations.RemoveConstraint(
            model_name="election",
            name="unique_election_per_cycle_office_phase",
        ),
        migrations.AddField(
            model_name="election",
            name="representative_category",
            field=models.CharField(
                blank=True,
                choices=[
                    ("general", "一般枠"),
                    ("corporate", "企業枠"),
                ],
                default="",
                max_length=20,
                verbose_name="代議員枠",
            ),
        ),
        migrations.AddConstraint(
            model_name="election",
            constraint=models.UniqueConstraint(
                fields=(
                    "cycle",
                    "office",
                    "phase",
                    "representative_category",
                ),
                name="unique_election_per_cycle_office_phase_category",
            ),
        ),
    ]
