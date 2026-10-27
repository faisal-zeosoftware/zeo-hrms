from django.urls import path
from .views import FieldsView, ImportView, DirectoryView, FormSettingsView
from .fieldhelp import FieldHelpView   # v1.16.0
from .people import UserNamesView   # v1.62.0 ui
from . import workbook_views as WB   # v1.62.0 workbook

urlpatterns = [
    path('api/fields/', FieldsView.as_view(), name='tools-fields'),
    path('api/import/', ImportView.as_view(), name='tools-import'),
    path('api/directory/', DirectoryView.as_view(), name='tools-directory'),
    path('api/form-settings/', FormSettingsView.as_view(), name='tools-form-settings'),
    path('api/field-help/', FieldHelpView.as_view(), name='tools-field-help'),   # v1.16.0
    path('api/user-names/', UserNamesView.as_view(), name='tools-user-names'),   # v1.62.0 ui
    # v1.62.0 workbook – Settings › Data workbook
    path('api/workbooks/', WB.WorkbookListView.as_view(), name='tools-workbooks'),
    path('api/workbooks/template/', WB.WorkbookTemplateView.as_view(), name='tools-workbook-template'),
    path('api/workbooks/<int:pk>/', WB.WorkbookDetailView.as_view(), name='tools-workbook'),
    path('api/workbooks/<int:pk>/errors/', WB.WorkbookErrorsView.as_view(), name='tools-workbook-errors'),
    path('api/workbooks/<int:pk>/runs/<int:run>/', WB.WorkbookRunView.as_view(), name='tools-workbook-run'),
    path('api/workbooks/<int:pk>/<str:action>/', WB.WorkbookActionView.as_view(), name='tools-workbook-action'),
]
