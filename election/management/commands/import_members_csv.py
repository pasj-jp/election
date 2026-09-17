from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from election.models import ElectionCycle
from election.services.member_import import (
    MemberImportError,
    import_members,
)


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

        try:
            result = import_members(path.read_bytes(), cycle, save=not dry_run)
        except MemberImportError as exc:
            raise CommandError(str(exc)) from exc

        for warning in result.warnings:
            self.stderr.write(self.style.WARNING(f"WARNING: {warning}"))
        self.stdout.write(f"File              : {path}")
        self.stdout.write(f"Encoding          : {result.encoding}")
        self.stdout.write(f"Election          : {cycle}")
        self.stdout.write(f"Total rows        : {result.total_count}")
        self.stdout.write(f"Eligible voters   : {result.eligible_count}")
        self.stdout.write(f"Corporate         : {result.corporate_count}")
        self.stdout.write(f"General           : {result.general_count}")
        self.stdout.write(f"Created           : {result.created_count}")
        self.stdout.write(f"Updated           : {result.updated_count}")
        self.stdout.write(f"Unchanged         : {result.unchanged_count}")
        self.stdout.write(f"Warnings          : {len(result.warnings)}")
        if dry_run:
            self.stdout.write(
                self.style.WARNING("*** DRY RUN: DBは変更していません ***")
            )
        else:
            self.stdout.write(self.style.SUCCESS("CSV名簿の取り込みが完了しました。"))
