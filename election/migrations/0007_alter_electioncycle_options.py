from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("election", "0006_alter_membersnapshot_options"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="electioncycle",
            options={
                "ordering": ["-year"],
                "verbose_name": "選挙年度",
                "verbose_name_plural": "選挙年度",
            },
        ),
    ]
