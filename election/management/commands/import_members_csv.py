import csv
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction

from election.models import ElectionCycle, MemberSnapshot


REQUIRED_COLUMNS = {
    "member_no": "会員番号",
    "full_name": "会員名",
    "employee_type": "会員種別",
    "business_category": "所属",
    "affiliation": "所属所属機関名",
    "email": "ＭＬ用メールアドレス",
}


def normalized(value):
    return str(value or "").strip()


def split_member_name(full_name, row_number):
    parts = normalized(full_name).split()
    if len(parts) < 2:
        raise CommandError(
            f"{row_number}行目: 会員名を姓と名に分割できません。"
        )
    return parts[0], " ".join(parts[1:])


def read_roster(path):
    last_error = None
    for encoding in ("utf-8-sig", "cp932"):
        try:
            with path.open(encoding=encoding, newline="") as source:
                return list(csv.DictReader(source)), encoding
        except UnicodeDecodeError as exc:
            last_error = exc
    raise CommandError(
        "CSVをUTF-8またはCP932として読み込めません。"
    ) from last_error


class Command(BaseCommand):
    help = "CSV名簿から会員情報をMemberSnapshotへ取り込みます"

    def add_arguments(self, parser):
        parser.add_argument("--cycle", type=int, required=True)
        parser.add_argument(
            "--file",
            type=Path,
            required=True,
            help="取り込むCSVファイルのパス",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="検証と集計だけを行い、DBを変更しません",
        )

    def handle(self, *args, **options):
        year = options["cycle"]
        path = options["file"].expanduser().resolve()
        dry_run = options["dry_run"]

        if not path.is_file():
            raise CommandError(f"CSVファイルが存在しません: {path}")

        try:
            cycle = ElectionCycle.objects.get(year=year)
        except ElectionCycle.DoesNotExist as exc:
            raise CommandError(
                f"{year}年度のElectionCycleが存在しません。"
                "先にDjango管理画面から作成してください。"
            ) from exc

        rows, encoding = read_roster(path)
        if not rows:
            raise CommandError("CSVに会員データがありません。")

        missing_columns = [
            column
            for column in REQUIRED_COLUMNS.values()
            if column not in set(rows[0])
        ]
        if missing_columns:
            raise CommandError(
                "CSVに必要な列がありません: "
                + ", ".join(missing_columns)
            )

        members = []
        member_numbers = set()
        warning_count = 0

        for row_number, row in enumerate(rows, start=2):
            member_no = normalized(row[REQUIRED_COLUMNS["member_no"]])
            if not member_no:
                raise CommandError(f"{row_number}行目: 会員番号が空です。")
            if member_no in member_numbers:
                raise CommandError(
                    f"{row_number}行目: 会員番号が重複しています: "
                    f"{member_no}"
                )
            member_numbers.add(member_no)

            last_name, first_name = split_member_name(
                row[REQUIRED_COLUMNS["full_name"]], row_number
            )
            employee_type = normalized(
                row[REQUIRED_COLUMNS["employee_type"]]
            )
            business_category = normalized(
                row[REQUIRED_COLUMNS["business_category"]]
            )
            affiliation = normalized(
                row[REQUIRED_COLUMNS["affiliation"]]
            )
            email = normalized(row[REQUIRED_COLUMNS["email"]])

            if email:
                try:
                    validate_email(email)
                except ValidationError as exc:
                    raise CommandError(
                        f"{row_number}行目: メールアドレスが不正です。"
                    ) from exc
            else:
                warning_count += 1
                self.stderr.write(
                    self.style.WARNING(
                        f"WARNING: {row_number}行目 "
                        f"({member_no}) のメールアドレスが空です。"
                    )
                )

            category = (
                MemberSnapshot.RepresentativeCategory.CORPORATE
                if business_category == "企業関係"
                else MemberSnapshot.RepresentativeCategory.GENERAL
            )
            members.append(
                {
                    "member_no": member_no,
                    "values": {
                        "last_name": last_name,
                        "first_name": first_name,
                        "email": email,
                        "affiliation": affiliation,
                        "employee_type": employee_type,
                        "business_category": business_category,
                        "representative_category": category,
                        "is_eligible_voter": employee_type.startswith(
                            "正会員"
                        ),
                    },
                }
            )

        existing = {
            item.member_no: item
            for item in MemberSnapshot.objects.filter(
                cycle=cycle, member_no__in=member_numbers
            )
        }
        created_count = updated_count = unchanged_count = 0
        for member in members:
            current = existing.get(member["member_no"])
            if current is None:
                created_count += 1
            elif any(
                getattr(current, field) != value
                for field, value in member["values"].items()
            ):
                updated_count += 1
            else:
                unchanged_count += 1

        corporate_count = sum(
            item["values"]["representative_category"]
            == MemberSnapshot.RepresentativeCategory.CORPORATE
            for item in members
        )
        eligible_count = sum(
            item["values"]["is_eligible_voter"] for item in members
        )

        self.stdout.write(f"File              : {path}")
        self.stdout.write(f"Encoding          : {encoding}")
        self.stdout.write(f"Election          : {cycle}")
        self.stdout.write(f"Total rows        : {len(members)}")
        self.stdout.write(f"Eligible voters   : {eligible_count}")
        self.stdout.write(f"Corporate         : {corporate_count}")
        self.stdout.write(f"General           : {len(members) - corporate_count}")
        self.stdout.write(f"Created           : {created_count}")
        self.stdout.write(f"Updated           : {updated_count}")
        self.stdout.write(f"Unchanged         : {unchanged_count}")
        self.stdout.write(f"Warnings          : {warning_count}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING("*** DRY RUN: DBは変更していません ***")
            )
            return

        with transaction.atomic():
            for member in members:
                MemberSnapshot.objects.update_or_create(
                    cycle=cycle,
                    member_no=member["member_no"],
                    defaults=member["values"],
                )

        self.stdout.write(self.style.SUCCESS("CSV名簿の取り込みが完了しました。"))

