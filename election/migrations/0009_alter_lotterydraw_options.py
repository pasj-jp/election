from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("election", "0008_alter_candidate_options"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="lotterydraw",
            options={
                "verbose_name": "抽選",
                "verbose_name_plural": "抽選",
            },
        ),
    ]
