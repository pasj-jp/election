from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("election", "0011_alter_voterparticipation_options"),
    ]

    operations = [
        migrations.AlterField(
            model_name="membersnapshot",
            name="member_no",
            field=models.CharField(
                max_length=32,
                verbose_name="会員番号",
            ),
        ),
        migrations.AlterField(
            model_name="membersnapshot",
            name="last_name",
            field=models.CharField(
                max_length=100,
                verbose_name="氏",
            ),
        ),
        migrations.AlterField(
            model_name="membersnapshot",
            name="first_name",
            field=models.CharField(
                max_length=100,
                verbose_name="名",
            ),
        ),
        migrations.AlterField(
            model_name="membersnapshot",
            name="email",
            field=models.EmailField(
                max_length=254,
                verbose_name="メールアドレス",
            ),
        ),
        migrations.AlterField(
            model_name="membersnapshot",
            name="employee_type",
            field=models.CharField(
                max_length=100,
                verbose_name="会員種別",
            ),
        ),
        migrations.AlterField(
            model_name="membersnapshot",
            name="business_category",
            field=models.CharField(
                blank=True,
                max_length=100,
                verbose_name="所属カテゴリー",
            ),
        ),
        migrations.AlterField(
            model_name="membersnapshot",
            name="representative_category",
            field=models.CharField(
                choices=[
                    ("general", "一般枠"),
                    ("corporate", "企業枠"),
                ],
                max_length=20,
                verbose_name="所属枠",
            ),
        ),
        migrations.AlterField(
            model_name="membersnapshot",
            name="is_eligible_voter",
            field=models.BooleanField(
                default=False,
                verbose_name="有権者資格",
            ),
        ),
    ]
