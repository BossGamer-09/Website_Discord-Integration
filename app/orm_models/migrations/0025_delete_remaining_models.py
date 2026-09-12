from django.db import migrations


class Migration(migrations.Migration):
    """Remove remaining dead models from Django state. Tables are dropped."""

    dependencies = [
        ('orm_models', '0024_cleanup_crud_permissions'),
        # These deps ensure the live models were re-registered in their new homes first
        ('org', '0014_privatenotifythread'),
        ('orgevents', '0002_organicevent'),
    ]

    operations = [
        migrations.DeleteModel(name='BotSettings'),
        migrations.DeleteModel(name='OrganicEvent'),
        migrations.DeleteModel(name='MembershipApplication'),
        migrations.DeleteModel(name='RSIVerification'),
        migrations.DeleteModel(name='PendingChannelClosure'),
        migrations.DeleteModel(name='CaptchaSession'),
        migrations.DeleteModel(name='EntombedKnight'),
        migrations.DeleteModel(name='PromotionHistory'),
        migrations.DeleteModel(name='PrivateNotifyThread'),
        migrations.DeleteModel(name='BotCrashRecovery'),
        migrations.DeleteModel(name='VoiceActivityExport'),
    ]
