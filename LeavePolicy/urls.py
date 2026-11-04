from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views
from . import register62   # v1.62.0 people
from . import weekend_change   # v1.62.6 (item 22)
from . import rules_views   # v1.65.0 (items 82–97)

router = DefaultRouter()
router.register(r'api/policies', views.PolicyViewSet, basename='leave-policy')
router.register(r'api/employee-policies', views.EmployeePolicyViewSet, basename='employee-leave-policy')

urlpatterns = [
    path('', include(router.urls)),
    path('api/effective/', views.EffectiveView.as_view()),
    path('api/coverage/', views.CoverageView.as_view()),                      # v1.62.1
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
    path('api/weekend-change/', weekend_change.WeekendChangeView.as_view()),   # v1.62.6 (item 22)
    path('api/working-days/', weekend_change.WorkingDaysView.as_view()),       # v1.62.6 (item 22): what decides the working days
    # v1.65.0 (items 82–97): leave rules – company settings, leave-type rules, HR-confirmed eligibility
    path('api/leave-rules/', rules_views.LeaveRulesView.as_view()),
    path('api/leave-rules/types/', rules_views.TypeRulesView.as_view()),
    path('api/leave-rules/settings/', rules_views.LeaveRulesView.as_view(), {'part': 'settings'}),   # PATCH from the shared form
    path('api/leave-rules/<int:pk>/', rules_views.LeaveRulesView.as_view()),                        # PATCH the rules of leave type <pk>
    path('api/leave-rules/eligibility/', rules_views.EligibilityView.as_view()),
]
