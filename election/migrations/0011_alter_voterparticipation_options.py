from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("election", "0010_alter_election_options"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="voterparticipation",
            options={
                "verbose_name": "有権者",
                "verbose_name_plural": "有権者",
            },
        ),
    ]
