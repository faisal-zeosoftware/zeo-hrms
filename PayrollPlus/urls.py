"""v1.18.0 – PayrollPlus API (see views.py)."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views as V
from . import workspace as WS   # v1.62.0 payroll
from . import air_tickets as AT   # v1.62.0 air ticket
from . import air_rules_views as ATR   # v1.66.0 (items 118–123) air ticket rules
from . import loan_desk as LD   # v1.62.0 talent-ops
from . import lending_views as LNV   # v1.67.11 (loans)
from . import effective_views as EFV   # v1.67.12 (effective)
from . import accrual_views as AC   # v1.62.7 (item 18) accrue now – pay later
from . import monthly_input_views as MIV   # v1.62.11 (item 18b) monthly inputs grid, bulk opening balances
from . import payctl_views as PCV   # v1.62.11 (items 228, 231) payroll controls
from . import bulk_settlement as BS   # v1.62.11 (item 230) bulk final settlement
from . import branch_schemes as BSC   # v1.62.11 (item 229) ADPF / DIFC DEWS per branch
from . import bankfile_views as BF   # v1.62.11 (item 227) bank file layouts
from . import run_payment_views as RPV   # v1.62.11 (item 227) payroll run payments, payment file layouts
from . import pay163_views as P163   # v1.63.0 (items 27, 28) salary split, recovery schedules

router = DefaultRouter()
router.register(r'statutory', V.StatutoryViewSet, basename='statutory')   # v1.31.0

urlpatterns = [
    path('api/', include(router.urls)),
    path('api/settings/', V.SettingsView.as_view()),
    path('api/rule-versions/', EFV.RuleVersionsView.as_view()),               # v1.67.12 (effective, item 64)
    path('api/pension/<int:pk>/', EFV.PensionView.as_view()),                  # v1.67.12 (effective, item 249)
    path('api/pension-settings/', EFV.PensionSettingsView.as_view()),          # v1.67.12 (effective, item 249)
    path('api/legal-rates/', V.LegalRatesView.as_view()),                     # v1.59.0
    path('api/branch-schemes/', BSC.BranchSchemesView.as_view()),             # v1.62.11 (item 229)
    path('api/branch-schemes/<str:action>/', BSC.BranchSchemesView.as_view()),
    path('api/runs/<int:run_id>/control/', V.RunControlView.as_view()),
    path('api/encashments/<int:pk>/steps/', V.EncashmentStepsView.as_view()),
    path('api/settlements/', V.SettlementsView.as_view()),
    path('api/settlements/eligible/', V.EligibleView.as_view()),
    path('api/settlements/<int:pk>/', V.SettlementView.as_view()),
    path('api/settlements/<int:pk>/<str:action>/', V.SettlementView.as_view()),
    path('api/settlements/<int:pk>/<str:action>/<int:line_id>/', V.SettlementView.as_view()),   # v1.62.0 EOS: DELETE …/lines/<id>/
    path('api/eos-settings/', V.EosSettingsView.as_view()),                                  # v1.62.11 (EOS settings)
    path('api/leavers/', V.LeaversView.as_view()),                                              # v1.62.0 EOS: Leavers workspace
    path('api/leavers/bulk/', BS.BulkLeaversView.as_view()),                                 # v1.62.11 (item 230)
    path('api/leavers/bulk/<str:action>/', BS.BulkLeaversView.as_view()),
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
    path('api/lending/settings/', LNV.SettingsView.as_view()),                          # v1.67.11 (loans)
    path('api/lending/check/', LNV.CheckView.as_view()),
    path('api/lending/advances/<int:pk>/<str:action>/', LNV.AdvanceView.as_view()),
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
    path('api/loan-desk/requests/', LD.RequestsView.as_view()),                        # v1.62.0 talent-ops loans & advances
    path('api/loan-desk/approvals/', LD.ApprovalsView.as_view()),
    path('api/loan-desk/repayments/', LD.RepaymentsView.as_view()),
    path('api/loan-desk/levels/', LD.LevelsView.as_view()),
    path('api/air-tickets/policies/', AT.PoliciesView.as_view()),                      # v1.62.0 air ticket
    path('api/air-tickets/allocations/', AT.AllocationsView.as_view()),
    path('api/air-tickets/requests/', AT.RequestsView.as_view()),
    path('api/air-tickets/approvals/', AT.ApprovalsView.as_view()),
    path('api/air-tickets/levels/', AT.LevelsView.as_view()),
    path('api/air-tickets/mine/', AT.MineView.as_view()),
    path('api/air-tickets/rules/', ATR.RulesView.as_view()),                           # v1.66.0 (items 118–123)
    path('api/air-tickets/rules/policy/<int:pk>/', ATR.PolicyRulesView.as_view()),
    path('api/air-tickets/rules/fares/', ATR.FaresView.as_view()),
    path('api/air-tickets/rules/fares/<int:pk>/', ATR.FaresView.as_view()),
    path('api/air-tickets/rules/check/', ATR.CheckView.as_view()),
    path('api/air-tickets/rules/provision/', ATR.ProvisionReportView.as_view()),
    path('api/air-tickets/rules/travel/', ATR.TravelView.as_view()),
    path('api/air-tickets/rules/pay-outside/', ATR.PayOutsideView.as_view()),
    path('api/run-payments/<int:pk>/', RPV.RunPaymentsView.as_view()),                    # v1.62.11 (item 227)
    path('api/run-payments/<int:pk>/file/', RPV.RunPaymentFileView.as_view()),
    path('api/run-payments/<int:pk>/mark/', RPV.RunPaymentMarkView.as_view()),
    path('api/payment-methods/', RPV.PaymentMethodsView.as_view()),
    path('api/payment-methods/<str:code>/', RPV.PaymentMethodView.as_view()),
    path('api/payment-layouts/', RPV.PaymentLayoutsView.as_view()),
    path('api/payment-layouts/preview/', RPV.PaymentLayoutPreviewView.as_view()),
    path('api/payment-layouts/check-sample/', RPV.PaymentLayoutSampleView.as_view()),
    path('api/payment-layouts/<str:code>/', RPV.PaymentLayoutView.as_view()),
    path('api/bank-files/', BF.BankFilesView.as_view()),                               # v1.62.11 (item 227)
    path('api/bank-files/profiles/', BF.BankFileProfilesView.as_view()),
    path('api/bank-files/profiles/<int:pk>/', BF.BankFileProfileView.as_view()),
    path('api/bank-files/preview/', BF.BankFilePreviewView.as_view()),
    path('api/bank-files/download/', BF.BankFileDownloadView.as_view()),
    path('api/accruals/', AC.AccrualsView.as_view()),                                  # v1.62.7 (item 18)
    path('api/accruals/components/<int:pk>/', AC.AccrualComponentView.as_view()),
    path('api/accruals/balances/', AC.AccrualBalancesView.as_view()),
    path('api/accruals/entries/', AC.AccrualEntriesView.as_view()),
    path('api/accruals/opening/', AC.AccrualOpeningView.as_view()),
    path('api/accruals/opening/template/', MIV.OpeningTemplateView.as_view()),         # v1.62.11 (item 18b)
    path('api/accruals/opening/import/', MIV.OpeningImportView.as_view()),
    path('api/accruals/opening/grid/<int:pk>/', MIV.OpeningGridView.as_view()),
    path('api/monthly-inputs/', MIV.MonthlyInputsView.as_view()),                      # v1.62.11 (item 18b)
    path('api/monthly-inputs/preview/', MIV.MonthlyInputsPreviewView.as_view()),
    path('api/monthly-inputs/template/', MIV.MonthlyInputsTemplateView.as_view()),
    path('api/monthly-inputs/import/', MIV.MonthlyInputsImportView.as_view()),
    path('api/monthly-inputs/components/', MIV.MonthlyInputComponentsView.as_view()),
    path('api/monthly-inputs/components/<int:pk>/', MIV.MonthlyInputComponentView.as_view()),
    path('api/payctl/settings/', PCV.SettingsView.as_view()),                          # v1.62.11 (items 228, 231)
    path('api/payctl/holds/', PCV.HoldsView.as_view()),
    path('api/payctl/holds/<int:pk>/<str:action>/', PCV.HoldView.as_view()),
    path('api/payctl/locks/', PCV.LocksView.as_view()),
    path('api/payctl/locks/<int:pk>/<str:action>/', PCV.LockView.as_view()),
    path('api/payctl/revisions/', PCV.RevisionsView.as_view()),
    path('api/payctl/revisions/salary/', PCV.RevisionSalaryView.as_view()),
    path('api/payctl/revisions/<int:pk>/<str:action>/', PCV.RevisionView.as_view()),
    path('api/payctl/pay-date/', PCV.PayDateView.as_view()),
    path('api/payctl/loan-limit/', PCV.LoanLimitView.as_view()),
    path('api/payctl/rehire/', PCV.RehireView.as_view()),
    path('api/payctl/rehire/<int:pk>/', PCV.RehireView.as_view()),
    path('api/payctl/import/', PCV.ImportView.as_view()),
    path('api/payctl/import/<str:what>/', PCV.ImportView.as_view()),
    path('api/recoveries/', P163.RecoveriesView.as_view()),                           # v1.63.0 (item 28)
    path('api/recoveries/preview/', P163.RecoveryPreviewView.as_view()),
    path('api/recoveries/components/', P163.RecoveryComponentsView.as_view()),
    path('api/recoveries/import/', P163.RecoveryImportView.as_view()),
    path('api/recoveries/import/<str:what>/', P163.RecoveryImportView.as_view()),
    path('api/recoveries/<int:pk>/', P163.RecoveryView.as_view()),
    path('api/recoveries/<int:pk>/<str:action>/', P163.RecoveryView.as_view()),
    path('api/pay-splits/', P163.SplitsView.as_view()),                               # v1.63.0 (item 27)
    path('api/pay-splits/methods/', P163.SplitMethodsView.as_view()),
    path('api/pay-splits/settings/', P163.SplitSettingsView.as_view()),
    path('api/pay-splits/iban-check/', P163.IbanCheckView.as_view()),
    path('api/pay-splits/import/', P163.SplitImportView.as_view()),
    path('api/pay-splits/import/<str:what>/', P163.SplitImportView.as_view()),
    path('api/pay-splits/<int:pk>/<str:action>/', P163.SplitView.as_view()),
]
