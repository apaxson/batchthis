from django.apps import AppConfig


class BatchthisConfig(AppConfig):
    name = 'apps.batchthis'
    label = 'batchthis'
    # Matches what 0001_initial.py actually used (plain AutoField). Without this,
    # Django compares against its own framework-wide default (BigAutoField since
    # 3.2) and makemigrations wants to "upgrade" every table's id field on every
    # run - unrelated churn that has nothing to do with whatever's actually being
    # changed. Not a behavior change: existing id columns already match this.
    default_auto_field = 'django.db.models.AutoField'
