from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Election
from .services.candidate_generation import generate_preliminary_candidates
from .services.voter_generation import generate_voters


@receiver(post_save, sender=Election)
def generate_participants_when_election_is_created(
    sender,
    instance,
    created,
    raw,
    **kwargs,
):
    if not created or raw:
        return

    generate_voters(instance)
    if instance.phase == Election.Phase.PRELIMINARY:
        generate_preliminary_candidates(instance)
