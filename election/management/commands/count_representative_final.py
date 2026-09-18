from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from election.models import Election, ElectionCycle
from election.services.counting import (
    commit_representative_final_count,
    preview_representative_final_count,
)


class Command(BaseCommand):
    help = "代議員本選挙を枠別に開票します"

    def add_arguments(self, parser):
        parser.add_argument("--cycle", type=int, required=True)
        parser.add_argument(
            "--category",
            choices=["general", "corporate"],
            required=True,
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        try:
            cycle = ElectionCycle.objects.get(year=options["cycle"])
            election = Election.objects.get(
                cycle=cycle,
                office=Election.Office.REPRESENTATIVE,
                phase=Election.Phase.FINAL,
                representative_category=options["category"],
            )
        except ElectionCycle.DoesNotExist as exc:
            raise CommandError("ElectionCycleが存在しません。") from exc
        except Election.DoesNotExist as exc:
            raise CommandError("指定した代議員本選挙が存在しません。") from exc

        try:
            preview = preview_representative_final_count(election)
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        result = preview["result"]
        self.stdout.write(f"Election : {election}")
        self.stdout.write(f"Ballots  : {preview['ballot_count']}")
        self.stdout.write(f"Seats    : {result['seats']}")
        for candidate in result["candidates"]:
            self.stdout.write(
                f"{candidate.vote_count:4} "
                f"{candidate.member.member_no:10} "
                f"{candidate.member.last_name} {candidate.member.first_name}"
            )
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("*** DRY RUN ***"))
            return

        try:
            commit_representative_final_count(election)
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("開票結果をDBへ保存しました。"))
