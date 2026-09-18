from django.core.management.base import CommandError

from election.models import Election


def add_category_argument(parser, *, required=False):
    parser.add_argument(
        "--category",
        choices=[
            Election.RepresentativeCategory.GENERAL,
            Election.RepresentativeCategory.CORPORATE,
        ],
        required=required,
        help="代議員選挙の枠",
    )


def get_category_option(options):
    category = options.get("category") or ""
    if (
        options["office"] == Election.Office.REPRESENTATIVE
        and not category
    ):
        raise CommandError("代議員選挙では--categoryを指定してください。")
    return category


def get_selected_election(cycle, options):
    category = get_category_option(options)
    try:
        return Election.objects.get(
            cycle=cycle,
            office=options["office"],
            phase=options["phase"],
            representative_category=category,
        )
    except Election.DoesNotExist as exc:
        raise CommandError("指定したElectionが存在しません。") from exc
