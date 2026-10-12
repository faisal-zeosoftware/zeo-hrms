from django.apps import AppConfig


class SavedViewsConfig(AppConfig):
    """v1.16.0 – saved list views (search, filters, grouping, sort, columns, rows per page, view mode) per user and screen."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'SavedViews'
    verbose_name = 'Saved list views'
