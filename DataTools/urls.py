from django.urls import path
from .views import FieldsView, ImportView, ImportJobView, DirectoryView, FormSettingsView
from .fieldhelp import FieldHelpView   # v1.16.0
from .people import UserNamesView   # v1.62.0 ui
from .usage import UsageView, ActiveView   # v1.62.1
from . import workbook_views as WB   # v1.62.0 workbook

urlpatterns = [
    path('api/fields/', FieldsView.as_view(), name='tools-fields'),
    path('api/import/', ImportView.as_view(), name='tools-import'),
    path('api/import/job/<str:token>/', ImportJobView.as_view(), name='tools-import-job'),   # v1.79.1 background import
    path('api/import/job/<str:token>/<str:action>/', ImportJobView.as_view(), name='tools-import-job-action'),
    path('api/directory/', DirectoryView.as_view(), name='tools-directory'),
    path('api/form-settings/', FormSettingsView.as_view(), name='tools-form-settings'),
    path('api/field-help/', FieldHelpView.as_view(), name='tools-field-help'),   # v1.16.0
    path('api/user-names/', UserNamesView.as_view(), name='tools-user-names'),   # v1.62.0 ui
    path('api/usage/', UsageView.as_view(), name='tools-usage'),   # v1.62.1 used in transactions?
    path('api/active/', ActiveView.as_view(), name='tools-active'),   # v1.62.1 make inactive / active
    # v1.62.0 workbook – Settings › Data workbook
    path('api/workbooks/', WB.WorkbookListView.as_view(), name='tools-workbooks'),
    path('api/workbooks/template/', WB.WorkbookTemplateView.as_view(), name='tools-workbook-template'),
    path('api/workbooks/demo/', WB.WorkbookDemoView.as_view(), name='tools-workbook-demo'),   # v1.67.25 (item 1.62.9)
    path('api/workbooks/<int:pk>/', WB.WorkbookDetailView.as_view(), name='tools-workbook'),
    path('api/workbooks/<int:pk>/errors/', WB.WorkbookErrorsView.as_view(), name='tools-workbook-errors'),
    path('api/workbooks/<int:pk>/runs/<int:run>/', WB.WorkbookRunView.as_view(), name='tools-workbook-run'),
    path('api/workbooks/<int:pk>/<str:action>/', WB.WorkbookActionView.as_view(), name='tools-workbook-action'),
]
