from django.apps import AppConfig


class ShiftPlannerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ShiftPlanner'
    verbose_name = 'Shift planner (roster, requests, open shifts)'
