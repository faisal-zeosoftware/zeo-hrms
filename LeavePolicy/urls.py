from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'api/policies', views.PolicyViewSet, basename='leave-policy')
router.register(r'api/employee-policies', views.EmployeePolicyViewSet, basename='employee-leave-policy')

urlpatterns = [
    path('', include(router.urls)),
    path('api/effective/', views.EffectiveView.as_view()),
    path('api/uae-setup/', views.UAESetupView.as_view()),
    path('api/accrual/', views.AccrualView.as_view()),
    path('api/year-end/', views.YearEndView.as_view()),
    path('api/ledger/', views.LedgerView.as_view()),
    path('api/ledger/start/', views.LedgerStartView.as_view()),
    path('api/openings/', views.OpeningsView.as_view()),
    path('api/planner/', views.PlannerView.as_view()),
    path('api/encashments/', views.EncashmentView.as_view()),
    path('api/approvers/', views.ApproversView.as_view()),
    path('api/who-approves/', views.WhoApprovesView.as_view()),
    path('api/escalations/', views.EscalationView.as_view()),
]
