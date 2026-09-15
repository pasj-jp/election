from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("election", "0005_voterparticipation_email_send_attempts_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="membersnapshot",
            options={
                "ordering": ["member_no"],
                "verbose_name": "会員リスト",
                "verbose_name_plural": "会員リスト",
            },
        ),
    ]
