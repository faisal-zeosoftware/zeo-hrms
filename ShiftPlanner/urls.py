"""v1.12.0 – Shift planner API, mounted at /shift-planner/ (see INTEGRATION.md)."""
from django.urls import path

from . import views as V

urlpatterns = [
    path('api/meta/', V.MetaView.as_view()),
    path('api/shift-master/', V.ShiftMasterView.as_view()),
    path('api/shift-master/<int:pk>/', V.ShiftMasterDetail.as_view()),
    path('api/periods/', V.PeriodsView.as_view()),
    path('api/periods/<int:pk>/', V.PeriodDetail.as_view()),
    path('api/periods/<int:pk>/grid/', V.GridView.as_view()),
    path('api/periods/<int:pk>/cells/', V.CellsView.as_view()),
    path('api/periods/<int:pk>/<str:action>/', V.PeriodAction.as_view()),
    path('api/availability/', V.AvailabilityView.as_view()),
    path('api/availability/<int:pk>/', V.AvailabilityDetail.as_view()),
    path('api/open-shifts/', V.OpenShiftsView.as_view()),
    path('api/open-shifts/<int:pk>/<str:action>/', V.OpenShiftAction.as_view()),
    path('api/claims/<int:pk>/<str:action>/', V.ClaimAction.as_view()),
    path('api/requests/', V.RequestsView.as_view()),
    path('api/requests/<int:pk>/<str:action>/', V.RequestAction.as_view()),
    path('api/my-schedule/', V.MyScheduleView.as_view()),
    path('api/team-schedule/', V.TeamScheduleView.as_view()),
    path('api/notices/', V.NoticesView.as_view()),
    path('api/resolve/', V.ResolveView.as_view()),
    path('api/payroll/', V.PayrollPreview.as_view()),
    path('api/upload/roster/', V.UploadRosterView.as_view()),
    path('api/upload/shifts/', V.UploadShiftsView.as_view()),
    path('api/template/<str:kind>/', V.TemplateView.as_view()),
    path('api/reports/roster/', V.RosterReport.as_view()),
    path('api/reports/requests/', V.RequestsReport.as_view()),
]
