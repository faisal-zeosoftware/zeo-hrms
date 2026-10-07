from django.urls import path

from .views import DrillView, Employee360View, EssDashboardView, OverviewView, TeamDashboardView

urlpatterns = [
    path('api/ess/', EssDashboardView.as_view(), name='dashboard-ess'),
    path('api/team/', TeamDashboardView.as_view(), name='dashboard-team'),
    path('api/drill/', DrillView.as_view(), name='dashboard-drill'),
    path('api/employee/<int:pk>/', Employee360View.as_view(), name='dashboard-employee'),
    path('api/overview/', OverviewView.as_view(), name='dashboard-overview'),
]
