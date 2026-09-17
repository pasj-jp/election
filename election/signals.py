from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Election
from .services.candidate_generation import generate_preliminary_candidates


@receiver(post_save, sender=Election)
def generate_candidates_when_preliminary_election_is_created(
    sender,
    instance,
    created,
    raw,
    **kwargs,
):
    if created and not raw and instance.phase == Election.Phase.PRELIMINARY:
        generate_preliminary_candidates(instance)
