from django.urls import path
from .views import FieldsView, ImportView, DirectoryView, FormSettingsView
from .fieldhelp import FieldHelpView   # v1.16.0

urlpatterns = [
    path('api/fields/', FieldsView.as_view(), name='tools-fields'),
    path('api/import/', ImportView.as_view(), name='tools-import'),
    path('api/directory/', DirectoryView.as_view(), name='tools-directory'),
    path('api/form-settings/', FormSettingsView.as_view(), name='tools-form-settings'),
    path('api/field-help/', FieldHelpView.as_view(), name='tools-field-help'),   # v1.16.0
]
