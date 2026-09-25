from django.core.exceptions import PermissionDenied

from .models import ElectionCycle


def accessible_cycles(user):
    """ユーザーが担当する年度。スーパーユーザーは全年度。"""
    if not user.is_authenticated or not user.is_staff:
        return ElectionCycle.objects.none()
    if user.is_superuser:
        return ElectionCycle.objects.all()
    return ElectionCycle.objects.filter(manager_groups__user=user).distinct()


def can_access_cycle(user, cycle):
    if user.is_superuser:
        return True
    return (
        user.is_authenticated
        and user.is_staff
        and cycle.manager_groups.filter(user=user).exists()
    )


def require_cycle_access(user, cycle):
    if not can_access_cycle(user, cycle):
        raise PermissionDenied("この年度の選挙を管理する権限がありません。")
