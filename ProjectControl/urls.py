from django.urls import path

from . import views as V

urlpatterns = [
    path('api/my-projects/', V.MyProjectsView.as_view()),
    path('api/projects/', V.ProjectListView.as_view()),
    path('api/week/', V.WeekView.as_view()),
    path('api/week/save/', V.WeekSaveView.as_view()),
    path('api/entries/billable/', V.EntryBillableView.as_view()),
    path('api/timer/', V.TimerView.as_view()),
    path('api/timer/start/', V.TimerStartView.as_view()),
    path('api/timer/stop/', V.TimerStopView.as_view()),
    path('api/submit/', V.SubmitView.as_view()),
    path('api/approvals/', V.ApprovalListView.as_view()),
    path('api/approvals/approve/', V.ApproveView.as_view()),
    path('api/approvals/reject/', V.RejectView.as_view()),
    path('api/finance/<int:pk>/', V.FinanceView.as_view()),
    path('api/project-summary/<int:pk>/', V.ProjectSummaryView.as_view()),
    path('api/employee-summary/', V.EmployeeSummaryView.as_view()),
    path('api/cost-rate/<int:pk>/', V.CostRateView.as_view()),
    path('api/settings/', V.SettingsView.as_view()),
]
