from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("election", "0007_alter_electioncycle_options"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="candidate",
            options={
                "verbose_name": "候補者",
                "verbose_name_plural": "候補者",
            },
        ),
    ]
