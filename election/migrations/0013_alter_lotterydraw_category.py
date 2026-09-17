from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("election", "0012_alter_membersnapshot_field_labels"),
    ]

    operations = [
        migrations.AlterField(
            model_name="lotterydraw",
            name="category",
            field=models.CharField(
                choices=[
                    ("general", "一般枠"),
                    ("corporate", "企業枠"),
                    ("president", "会長"),
                ],
                max_length=20,
            ),
        ),
    ]
