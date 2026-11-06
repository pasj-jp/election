from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("election", "0017_electioncycle_periods"),
    ]

    operations = [
        migrations.AddField(
            model_name="electioncycle",
            name="manager_groups",
            field=models.ManyToManyField(
                blank=True,
                help_text="この年度を閲覧・編集できるグループを選択します。",
                related_name="managed_election_cycles",
                to="auth.group",
                verbose_name="選挙管理委員グループ",
            ),
        ),
    ]
