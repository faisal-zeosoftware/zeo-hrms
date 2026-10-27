from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views
from . import register62   # v1.62.0 people

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
    path('api/balance-explained/', views.BalanceExplainedView.as_view()),   # v1.49.0
    path('api/check/', views.LeaveCheckView.as_view()),                      # v1.51.0 leave conflicts
    path('api/conflicts/', views.ConflictSettingsView.as_view()),
    path('api/conflicts/<int:pk>/', views.ConflictSettingsView.as_view()),
    path('api/ledger/start/', views.LedgerStartView.as_view()),
    path('api/openings/', views.OpeningsView.as_view()),
    path('api/planner/', views.PlannerView.as_view()),
    path('api/encashments/', views.EncashmentView.as_view()),
    path('api/approvers/', views.ApproversView.as_view()),
    path('api/who-approves/', views.WhoApprovesView.as_view()),
    path('api/escalations/', views.EscalationView.as_view()),
    # v1.62.0 people: leave requests register (stage, waiting for me, balance after) and the balance grid
    path('api/requests/', register62.LeaveRequestsView.as_view()),
    path('api/requests/stats/', register62.LeaveRequestStatsView.as_view()),
    path('api/requests/<int:pk>/<str:action>/', register62.LeaveRequestActionView.as_view()),
    path('api/balance-grid/', register62.BalanceGridView.as_view()),
]
