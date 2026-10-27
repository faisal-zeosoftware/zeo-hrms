"""v1.18.0 – PayrollPlus API (see views.py)."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views as V
from . import workspace as WS   # v1.62.0 payroll
from . import air_tickets as AT   # v1.62.0 air ticket

router = DefaultRouter()
router.register(r'statutory', V.StatutoryViewSet, basename='statutory')   # v1.31.0

urlpatterns = [
    path('api/', include(router.urls)),
    path('api/settings/', V.SettingsView.as_view()),
    path('api/legal-rates/', V.LegalRatesView.as_view()),                     # v1.59.0
    path('api/runs/<int:run_id>/control/', V.RunControlView.as_view()),
    path('api/encashments/<int:pk>/steps/', V.EncashmentStepsView.as_view()),
    path('api/settlements/', V.SettlementsView.as_view()),
    path('api/settlements/eligible/', V.EligibleView.as_view()),
    path('api/settlements/<int:pk>/', V.SettlementView.as_view()),
    path('api/settlements/<int:pk>/<str:action>/', V.SettlementView.as_view()),
    path('api/settlements/<int:pk>/<str:action>/<int:line_id>/', V.SettlementView.as_view()),   # v1.62.0 EOS: DELETE …/lines/<id>/
    path('api/leavers/', V.LeaversView.as_view()),                                              # v1.62.0 EOS: Leavers workspace
    path('api/leavers/mine/', V.MyLeaverView.as_view()),
    path('api/leavers/mine/<str:action>/', V.MyLeaverView.as_view()),
    path('api/leavers/<int:pk>/', V.LeaverView.as_view()),
    path('api/leavers/<int:pk>/<str:action>/', V.LeaverView.as_view()),
    path('api/provisions/', V.ProvisionsView.as_view()),                      # v1.49.0
    path('api/provisions/<int:pk>/', V.ProvisionView.as_view()),
    path('api/provisions/<int:pk>/<str:action>/', V.ProvisionView.as_view()),
    path('api/loans/', V.LoansView.as_view()),
    path('api/loans/<int:pk>/', V.LoanView.as_view()),
    path('api/loans/<int:pk>/<str:action>/', V.LoanView.as_view()),
    path('api/what-if/', V.WhatIfsView.as_view()),                           # v1.50.0
    path('api/what-if/<int:pk>/', V.WhatIfView.as_view()),
    path('api/what-if/<int:pk>/<str:action>/', V.WhatIfView.as_view()),
    path('api/cost-allocation/rules/', V.CostAllocRulesView.as_view()),
    path('api/cost-allocation/rules/<int:pk>/', V.CostAllocRuleView.as_view()),
    path('api/cost-allocation/', V.CostAllocMonthView.as_view()),
    path('api/pay-calendars/', V.CalendarsView.as_view()),                   # v1.55.0
    path('api/pay-calendars/<int:pk>/', V.CalendarView.as_view()),
    path('api/off-cycle/', V.OffCycleView.as_view()),
    path('api/arrears/', V.ArrearsView.as_view()),
    path('api/workspace/runs/', WS.WorkspaceRunsView.as_view()),                       # v1.62.0 payroll
    path('api/workspace/runs/<int:pk>/', WS.WorkspaceRunView.as_view()),
    path('api/workspace/runs/<int:pk>/wps-made/', WS.WorkspaceWpsMadeView.as_view()),
    path('api/workspace/approvals/', WS.WorkspaceApprovalsView.as_view()),
    path('api/workspace/salaries/', WS.WorkspaceSalariesView.as_view()),
    path('api/workspace/components/', WS.WorkspaceComponentsView.as_view()),
    path('api/air-tickets/policies/', AT.PoliciesView.as_view()),                      # v1.62.0 air ticket
    path('api/air-tickets/allocations/', AT.AllocationsView.as_view()),
    path('api/air-tickets/requests/', AT.RequestsView.as_view()),
    path('api/air-tickets/approvals/', AT.ApprovalsView.as_view()),
    path('api/air-tickets/levels/', AT.LevelsView.as_view()),
    path('api/air-tickets/mine/', AT.MineView.as_view()),
]
