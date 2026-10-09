from django.urls import path

from .assistant import AssistantView
from .records import RecordView
from .reports import ReportEmailView, ReportListView, ReportView
from .ceo import CeoDashboardView
from .unified_calendar import CalendarView
from .views import DrillView, Employee360View, EssDashboardView, OverviewView, TeamDashboardView

urlpatterns = [
    path('api/ess/', EssDashboardView.as_view(), name='dashboard-ess'),
    path('api/team/', TeamDashboardView.as_view(), name='dashboard-team'),
    path('api/drill/', DrillView.as_view(), name='dashboard-drill'),
    path('api/employee/<int:pk>/', Employee360View.as_view(), name='dashboard-employee'),
    path('api/ceo/', CeoDashboardView.as_view(), name='dashboard-ceo'),
    path('api/calendar/', CalendarView.as_view(), name='dashboard-calendar'),
    path('api/overview/', OverviewView.as_view(), name='dashboard-overview'),
    path('api/assistant/', AssistantView.as_view(), name='assistant'),
    path('api/record/<str:label>/<int:pk>/', RecordView.as_view(), name='record'),
    path('api/reports/', ReportListView.as_view(), name='report-list'),
    path('api/reports/<slug:key>/', ReportView.as_view(), name='report-run'),
    path('api/reports/<slug:key>/email/', ReportEmailView.as_view(), name='report-email'),
]
