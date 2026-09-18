from django.db import migrations, models


def mark_existing_votes_as_electronic(apps, schema_editor):
    VoterParticipation = apps.get_model(
        "election", "VoterParticipation"
    )
    VoterParticipation.objects.filter(
        voted_at__isnull=False,
        voting_method="",
    ).update(voting_method="electronic")


class Migration(migrations.Migration):

    dependencies = [
        ("election", "0015_candidate_manifesto"),
    ]

    operations = [
        migrations.AddField(
            model_name="ballot",
            name="voting_method",
            field=models.CharField(
                choices=[
                    ("electronic", "電子投票"),
                    ("paper", "書面投票"),
                ],
                default="electronic",
                max_length=20,
                verbose_name="投票方法",
            ),
        ),
        migrations.AddField(
            model_name="voterparticipation",
            name="voting_method",
            field=models.CharField(
                blank=True,
                choices=[
                    ("electronic", "電子投票"),
                    ("paper", "書面投票"),
                ],
                default="",
                max_length=20,
                verbose_name="投票方法",
            ),
        ),
        migrations.RunPython(
            mark_existing_votes_as_electronic,
            migrations.RunPython.noop,
        ),
    ]
