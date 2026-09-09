import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ldap3 import Connection, Server, SUBTREE

from election.models import ElectionCycle, MemberSnapshot


LDAP_ATTRIBUTES = [
    "uid",
    "sn",
    "givenName",
    "mail",
    "employeeType",
    "businessCategory",
    "o",
]


def ldap_value(entry, name):
    """
    LDAP属性を安全に文字列として取得する。
    属性が存在しない場合は空文字を返す。
    """
    try:
        value = entry[name].value
    except (KeyError, AttributeError):
        return ""

    if value is None:
        return ""

    if isinstance(value, list):
        return str(value[0]) if value else ""

    return str(value).strip()


def is_regular_member(employee_type):
    """
    選挙権判定。

    例:
      正会員（一般）
      正会員（...）

    を正会員として扱う。
    """
    return employee_type.strip().startswith("正会員")


class Command(BaseCommand):
    help = "LDAPから会員情報を取得してMemberSnapshotへ同期します"

    def add_arguments(self, parser):
        parser.add_argument(
            "--cycle",
            type=int,
            required=True,
            help="選挙年度。例: --cycle 2027",
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="DBを変更せず同期予定内容だけ確認します",
        )

    def handle(self, *args, **options):
        year = options["cycle"]
        dry_run = options["dry_run"]

        try:
            cycle = ElectionCycle.objects.get(year=year)
        except ElectionCycle.DoesNotExist:
            raise CommandError(
                f"{year}年度のElectionCycleが存在しません。"
                "先にDjango管理画面から作成してください。"
            )

        ldap_uri = os.environ.get(
            "ELECTION_LDAP_URI",
            "ldap://localhost:389",
        )

        ldap_base_dn = os.environ.get(
            "ELECTION_LDAP_BASE_DN",
            "ou=people,dc=pasj,dc=jp",
        )

        ldap_bind_dn = os.environ.get(
            "ELECTION_LDAP_BIND_DN",
            "",
        )

        ldap_password = os.environ.get(
            "ELECTION_LDAP_PASSWORD",
            "",
        )

        self.stdout.write(
            f"LDAP server : {ldap_uri}"
        )
        self.stdout.write(
            f"Base DN     : {ldap_base_dn}"
        )
        self.stdout.write(
            f"Election    : {cycle}"
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING("*** DRY RUN ***")
            )

        server = Server(ldap_uri)

        try:
            if ldap_bind_dn:
                conn = Connection(
                    server,
                    user=ldap_bind_dn,
                    password=ldap_password,
                    auto_bind=True,
                )
            else:
                conn = Connection(
                    server,
                    auto_bind=True,
                )
        except Exception as exc:
            raise CommandError(
                f"LDAP接続に失敗しました: {exc}"
            )

        try:
            conn.search(
                search_base=ldap_base_dn,
                search_filter="(uid=*)",
                search_scope=SUBTREE,
                attributes=LDAP_ATTRIBUTES,
	    )

            if conn.result["result"] != 0:
                raise CommandError(
                    f"LDAP検索に失敗しました: {conn.result}"
                )

            entries = conn.entries

            if not entries:
                raise CommandError(
                    "LDAP検索は成功しましたが、該当する会員が0件でした。"
                )

            entries = conn.entries

            self.stdout.write(
                f"LDAP entries: {len(entries)}"
            )

            created_count = 0
            updated_count = 0
            unchanged_count = 0
            skipped_count = 0

            corporate_count = 0
            general_count = 0
            eligible_count = 0

            with transaction.atomic():

                for entry in entries:
                    member_no = ldap_value(entry, "uid")

                    if not member_no:
                        skipped_count += 1
                        self.stderr.write(
                            self.style.WARNING(
                                f"SKIP: uidなし: {entry.entry_dn}"
                            )
                        )
                        continue

                    last_name = ldap_value(entry, "sn")
                    first_name = ldap_value(entry, "givenName")
                    email = ldap_value(entry, "mail")

                    employee_type = ldap_value(
                        entry,
                        "employeeType",
                    )

                    business_category = ldap_value(
                        entry,
                        "businessCategory",
                    )

                    organization = ldap_value(
                        entry,
                        "o",
                    )

                    #
                    # 企業枠判定
                    #
                    if business_category == "企業関係":
                        representative_category = (
                            MemberSnapshot
                            .RepresentativeCategory
                            .CORPORATE
                        )
                        corporate_count += 1
                    else:
                        representative_category = (
                            MemberSnapshot
                            .RepresentativeCategory
                            .GENERAL
                        )
                        general_count += 1

                    #
                    # 選挙権判定
                    #
                    eligible = is_regular_member(
                        employee_type
                    )

                    if eligible:
                        eligible_count += 1

                    values = {
                        "last_name": last_name,
                        "first_name": first_name,
                        "email": email,

                        # o を所属機関名として保存
                        "affiliation": organization,

                        "employee_type": employee_type,
                        "business_category":
                            business_category,

                        "representative_category":
                            representative_category,

                        "is_eligible_voter":
                            eligible,
                    }

                    if dry_run:
                        category_display = (
                            "企業枠"
                            if representative_category
                            == MemberSnapshot
                            .RepresentativeCategory
                            .CORPORATE
                            else "一般枠"
                        )

                        voter_display = (
                            "選挙権あり"
                            if eligible
                            else "選挙権なし"
                        )

                        self.stdout.write(
                            f"{member_no:10} "
                            f"{last_name} {first_name} "
                            f"[{employee_type}] "
                            f"[{business_category}] "
                            f"{category_display} "
                            f"{voter_display}"
                        )

                        continue

                    obj, created = (
                        MemberSnapshot.objects.update_or_create(
                            cycle=cycle,
                            member_no=member_no,
                            defaults=values,
                        )
                    )

                    if created:
                        created_count += 1
                    else:
                        updated_count += 1

            self.stdout.write("")
            self.stdout.write(
                self.style.SUCCESS(
                    "LDAP synchronization completed."
                )
            )

            self.stdout.write(
                f"Total LDAP entries : {len(entries)}"
            )
            self.stdout.write(
                f"Eligible voters    : {eligible_count}"
            )
            self.stdout.write(
                f"Corporate          : {corporate_count}"
            )
            self.stdout.write(
                f"General            : {general_count}"
            )
            self.stdout.write(
                f"Skipped            : {skipped_count}"
            )

            if not dry_run:
                self.stdout.write(
                    f"Created            : {created_count}"
                )
                self.stdout.write(
                    f"Updated            : {updated_count}"
                )

        finally:
            conn.unbind()
