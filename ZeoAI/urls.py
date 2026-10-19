from django.urls import path

from . import views as V

urlpatterns = [
    path('api/settings/', V.SettingsView.as_view()),
    path('api/payroll/runs/', V.PayrollRunsView.as_view()),
    path('api/payroll/runs/<int:run_id>/audit/', V.PayrollAuditView.as_view()),
    path('api/payroll/runs/<int:run_id>/audit.xlsx', V.AuditXlsxView.as_view()),
    path('api/payroll/findings/<int:pk>/', V.FindingView.as_view()),
    path('api/attendance/patterns/', V.PatternsView.as_view()),
    path('api/attendance/coverage/', V.CoverageView.as_view()),
    path('api/attendance/replacements/', V.ReplacementsView.as_view()),
    path('api/attendance/roster/', V.RosterView.as_view()),
    path('api/recruitment/metrics/', V.RecruitMetricsView.as_view()),
    path('api/recruitment/candidates/<int:pk>/', V.CandidateView.as_view()),
    path('api/recruitment/jobs/<int:pk>/<str:what>/', V.JobAIView.as_view()),
    path('api/recruitment/applications/<int:pk>/draft/', V.DraftView.as_view()),
    path('api/performance/employees/<int:pk>/plan/save/', V.PlanSaveView.as_view()),
    path('api/performance/employees/<int:pk>/<str:what>/', V.EmployeeAIView.as_view()),
    path('api/performance/template/', V.TemplateView.as_view()),
    path('api/performance/training/', V.TrainingView.as_view()),
    path('api/performance/certificates/', V.CertificatesView.as_view()),
    path('api/workforce/ask/', V.AskView.as_view()),
    path('api/workforce/<str:what>/', V.WorkforceView.as_view()),
    path('api/log/', V.LogView.as_view()),
    path('api/lookups/', V.LookupsView.as_view()),
]
