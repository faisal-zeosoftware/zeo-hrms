from django.urls import path

from . import views as V

urlpatterns = [
    path('api/schedules/', V.SchedulesView.as_view()),                   # scheduled reports
    path('api/schedules/<int:pk>/', V.ScheduleView.as_view()),
    path('api/schedules/<int:pk>/<str:action>/', V.ScheduleView.as_view()),
    path('api/api-keys/', V.ApiKeysView.as_view()),                      # API keys
    path('api/api-keys/<int:pk>/', V.ApiKeyView.as_view()),
    path('api/webhooks/', V.WebhooksView.as_view()),                     # webhooks
    path('api/webhooks/<int:pk>/', V.WebhookView.as_view()),
    path('api/webhooks/<int:pk>/<str:action>/', V.WebhookView.as_view()),
    path('api/import-mapping/', V.ImportMappingView.as_view()),          # import memory and undo
    path('api/imports/', V.ImportsView.as_view()),
    path('api/imports/<int:pk>/undo/', V.ImportUndoView.as_view()),
    path('api/out-of-office/', V.OutOfOfficeView.as_view()),             # out of office
    path('api/out-of-office/<int:pk>/', V.OutOfOfficeView.as_view()),
    path('api/health/', V.HealthView.as_view()),                         # health checks
    path('api/retention/', V.RetentionView.as_view()),                    # v1.53.0 data retention and privacy
    path('api/retention/anonymise/', V.AnonymiseView.as_view()),
    path('api/legal-holds/', V.LegalHoldsView.as_view()),                 # v1.61.0
    path('api/privacy/export/me/', V.PrivacyExportView.as_view()),
    path('api/privacy/export/<int:emp_id>/', V.PrivacyExportView.as_view()),
    path('api/test-company/', V.TestCompanyView.as_view()),              # v1.53.0 test company with the setup
    path('api/test-company/<int:pk>/', V.TestCompanyView.as_view()),
    path('api/setup-guide/', V.SetupGuideView.as_view()),               # v1.56.0 setup guide
    path('api/setup-guide/home/', V.SetupGuideHomeView.as_view()),
    path('api/setup-guide/matrix/', V.SetupGuideMatrixView.as_view()),
    path('api/setup-guide/template/<str:kind>/', V.SetupGuideTemplateView.as_view()),
    path('api/setup-guide/<str:key>/', V.SetupGuideStepView.as_view()),
    path('api/history/', V.HistoryView.as_view()),                       # v1.62.0 log and history cleaning
    path('api/history/<int:pk>/archive/', V.HistoryArchiveView.as_view()),
    path('api/history/mine/', V.MyHistoryView.as_view()),
    path('api/bank-changes/', V.BankChangesView.as_view()),              # v1.62.0 a second person for bank-detail changes
    path('api/access/', V.AccessView.as_view()),                         # v1.62.0 give access
    path('api/access/slips/<str:key>/', V.AccessSlipsView.as_view()),
    path('api/exports/', V.ExportLogView.as_view()),                      # v1.62.0 export log
    path('api/exports/check/', V.ExportCheckView.as_view()),              # v1.62.0 exports made in the browser ask first
]
