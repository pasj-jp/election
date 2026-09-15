from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("election", "0009_alter_lotterydraw_options"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="election",
            options={
                "ordering": ["cycle", "phase", "office"],
                "verbose_name": "選挙",
                "verbose_name_plural": "選挙",
            },
        ),
    ]
