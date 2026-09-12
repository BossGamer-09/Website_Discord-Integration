"""
Data migration: remove stale content types left behind when models were moved
out of orm_models into their proper apps (disfunction, candidacy, sc_tracker).

These orphaned content types cause duplicate, unlabelled permission entries to
appear in the Django admin group permission picker (e.g. "nomination | Can add
Nomination" with no app label instead of "Nominations, Voice & Attendance |
Nomination | Can add Nomination").

Deleting the stale content type cascades to delete the associated Permission
rows, cleaning up the picker automatically.
"""
from django.db import migrations


# (app_label, model) pairs that were removed from orm_models and now live
# properly under their real app. We delete the OLD orm_models content type;
# the real one already exists under the correct app_label.
STALE_ORM_MODELS = [
    # moved to disfunction
    "nomination",
    "userdistinction",
    "distinctionlevel",
    "eventattendance",
    "eventattendancerecord",
    "eventreference",
    "historicalvoiceban",
    "voiceban",
    "voicesession",
    "voicesessioncheckpoint",
    "voiceactivitysummary",
    "temporaryvoicechannel",
    # moved to candidacy
    "squiretrialthread",
    # moved to sc_tracker
    "hangarstatusmessage",
    "rsiissue",
    "statusmessage",
    "statusdmmessage",
    "statussubscription",
    # other deleted models with no home
    "devtestlog",
    "userjoinrecord",
    "botcrashrecovery",
    "voiceactivityexport",
]


def delete_stale_content_types(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    deleted = (
        ContentType.objects
        .filter(app_label="orm_models", model__in=STALE_ORM_MODELS)
        .delete()
    )
    print(f"\n  Deleted {deleted[0]} stale orm_models content type(s).")


class Migration(migrations.Migration):

    dependencies = [
        ('orm_models', '0022_delete_nomination_models'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(
            delete_stale_content_types,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
