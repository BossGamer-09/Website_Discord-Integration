from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import DisciplinaryRecord, DecisionLog


@receiver(post_save, sender=DisciplinaryRecord)
def create_decision_log_for_discipline(sender, instance, created, **kwargs):
    if not created:
        return

    type_map = {
        DisciplinaryRecord.ActionType.WARN:       DecisionLog.EventType.DISCIPLINE,
        DisciplinaryRecord.ActionType.MUTE:       DecisionLog.EventType.DISCIPLINE,
        DisciplinaryRecord.ActionType.KICK:       DecisionLog.EventType.DISCIPLINE,
        DisciplinaryRecord.ActionType.BAN:        DecisionLog.EventType.DISCIPLINE,
        DisciplinaryRecord.ActionType.BLACKLIST:  DecisionLog.EventType.DISCIPLINE,
        DisciplinaryRecord.ActionType.INACTIVE:   DecisionLog.EventType.STAFF_ACTION,
        DisciplinaryRecord.ActionType.REPRIMAND:  DecisionLog.EventType.DISCIPLINE,
        DisciplinaryRecord.ActionType.NOTE:       DecisionLog.EventType.STAFF_ACTION,
        DisciplinaryRecord.ActionType.FORCE_ROLE: DecisionLog.EventType.ROLE_ASSIGN,
    }

    DecisionLog.objects.create(
        event_type=type_map.get(instance.action_type, DecisionLog.EventType.STAFF_ACTION),
        subject=instance.subject,
        actor=instance.issued_by,
        summary=f"{instance.get_action_type_display()}: {instance.reason[:200]}",
        disciplinary_record=instance,
    )
