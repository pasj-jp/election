from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("election", "0014_election_representative_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="candidate",
            name="manifesto",
            field=models.TextField(blank=True, verbose_name="抱負"),
        ),
    ]
