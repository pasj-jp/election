import csv
from dataclasses import dataclass
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from election.models import MemberSnapshot


REQUIRED_COLUMNS = {
    "member_no": "会員番号",
    "full_name": "会員名",
    "employee_type": "会員種別",
    "business_category": "所属",
    "affiliation": "所属所属機関名",
    "email": "ＭＬ用メールアドレス",
}


class MemberImportError(ValueError):
    pass


@dataclass(frozen=True)
class MemberImportResult:
    encoding: str
    total_count: int
    eligible_count: int
    corporate_count: int
    created_count: int
    updated_count: int
    unchanged_count: int
    warnings: tuple[str, ...]

    @property
    def general_count(self):
        return self.total_count - self.corporate_count


def normalized(value):
    return str(value or "").strip()


def split_member_name(full_name, row_number):
    parts = normalized(full_name).split()
    if len(parts) < 2:
        raise MemberImportError(
            f"{row_number}行目: 会員名を姓と名に分割できません。"
        )
    return parts[0], " ".join(parts[1:])


def decode_roster(content):
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise MemberImportError("CSVをUTF-8またはCP932として読み込めません。")


def import_members(content, cycle, *, save=True):
    text, encoding = decode_roster(content)
    try:
        rows = list(csv.DictReader(StringIO(text, newline="")))
    except csv.Error as exc:
        raise MemberImportError(f"CSVの形式が不正です: {exc}") from exc
    if not rows:
        raise MemberImportError("CSVに会員データがありません。")

    missing_columns = [
        column
        for column in REQUIRED_COLUMNS.values()
        if column not in set(rows[0])
    ]
    if missing_columns:
        raise MemberImportError(
            "CSVに必要な列がありません: " + ", ".join(missing_columns)
        )

    members = []
    member_numbers = set()
    warnings = []
    for row_number, row in enumerate(rows, start=2):
        member_no = normalized(row[REQUIRED_COLUMNS["member_no"]])
        if not member_no:
            raise MemberImportError(f"{row_number}行目: 会員番号が空です。")
        if member_no in member_numbers:
            raise MemberImportError(
                f"{row_number}行目: 会員番号が重複しています: {member_no}"
            )
        member_numbers.add(member_no)

        last_name, first_name = split_member_name(
            row[REQUIRED_COLUMNS["full_name"]], row_number
        )
        employee_type = normalized(row[REQUIRED_COLUMNS["employee_type"]])
        business_category = normalized(
            row[REQUIRED_COLUMNS["business_category"]]
        )
        affiliation = normalized(row[REQUIRED_COLUMNS["affiliation"]])
        email = normalized(row[REQUIRED_COLUMNS["email"]])
        if email:
            try:
                validate_email(email)
            except ValidationError as exc:
                raise MemberImportError(
                    f"{row_number}行目: メールアドレスが不正です。"
                ) from exc
        else:
            warnings.append(
                f"{row_number}行目 ({member_no}) のメールアドレスが空です。"
            )

        category = (
            MemberSnapshot.RepresentativeCategory.CORPORATE
            if business_category == "企業関係"
            else MemberSnapshot.RepresentativeCategory.GENERAL
        )
        members.append({
            "member_no": member_no,
            "values": {
                "last_name": last_name,
                "first_name": first_name,
                "email": email,
                "affiliation": affiliation,
                "employee_type": employee_type,
                "business_category": business_category,
                "representative_category": category,
                "is_eligible_voter": employee_type.startswith("正会員"),
            },
        })

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
    if save:
        with transaction.atomic():
            for member in members:
                MemberSnapshot.objects.update_or_create(
                    cycle=cycle,
                    member_no=member["member_no"],
                    defaults=member["values"],
                )

    return MemberImportResult(
        encoding=encoding,
        total_count=len(members),
        eligible_count=eligible_count,
        corporate_count=corporate_count,
        created_count=created_count,
        updated_count=updated_count,
        unchanged_count=unchanged_count,
        warnings=tuple(warnings),
    )
