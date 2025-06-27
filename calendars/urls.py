from django.urls import path, include
from .  views import (WeekendViewset,AssignWeekendViewset,HolidayViewset,HolidayCalendarViewset,HolidayAssignViewset,WeekendDetailsViewset,Applicableviewset,LeaveTypeviewset,LeaveEntitlementviewset,LeaveRequestviewset,LeaveTypeViewSet,leave_balance_viewset,Acrualviewset,Resetviewset,
                    AttendanceViewSet,ShiftViewSet,ImportAttendanceViewSet,EmployeeMachineMappingViewset,Leave_ReportViewset,LvApprovalLevelViewset,
                    LvApprovalViewset,LvEmailTemplateviewset,LvApprovalNotifyviewset,LvCommonWorkflowViewset,LvRejectionViewset,Lv_Approval_ReportViewset,
                    AttendanceReportViewset,LvBalanceReportViewset,EmployeeYearlyCalendarViewset,ShiftPatternViewSet,EmployeeShiftScheduleViewSet,
                    ShiftOverrideViewSet,LeaveResetPolicyviewset,LeaveCarryForwardTransactionviewset,LeaveEncashmentTransactionviewset,EmpOpeningsBlkupldViewSet,ApplyOpeningsAPIView,EmployeeRejoiningViewset,ImmediateRejectAPIView,EmployeeAttendanceSummaryAPIView,MonthwiseAccrualSimulationView,
                    LeaveResetPreviewAPIView,EmployeeOvertimeViewset,MonthlyAttendanceSummaryViewSet
                    )

from rest_framework.routers import DefaultRouter
router = DefaultRouter()

router.register(r'weekend', WeekendViewset)
router.register(r'assign-days', WeekendDetailsViewset)
router.register(r'assign-weekend', AssignWeekendViewset)
router.register(r'holiday', HolidayViewset)
router.register(r'holiday-calendar', HolidayCalendarViewset)
router.register(r'assign-holiday', HolidayAssignViewset)

router.register(r'applicable_to', Applicableviewset, basename='leave_applicable')
router.register(r'Leave_balance', leave_balance_viewset, basename='accrual')
router.register(r'accrual', Acrualviewset, basename='acruak')
router.register(r'reset', Resetviewset, basename='reset')
# router.register(r'enchash', Enchashviewset, basename='enchash')
router.register(r'leave-type', LeaveTypeviewset, basename='leave_type')
router.register(r'leave-entitlement', LeaveEntitlementviewset, basename='leave_entitlement')
router.register(r'leave-reset-policy', LeaveResetPolicyviewset, basename='leave_reset_policy')
router.register(r'leave-carry-forward-transaction', LeaveCarryForwardTransactionviewset, basename='leave_carry_forward_transaction')
router.register(r'leave-encashment-tranaction',LeaveEncashmentTransactionviewset, basename='leave_encashment_transaction')
# router.register(r'leave-policy', LeavePolicyviewset, basename='leave_policy')
router.register(r'emp-leave-request', LeaveRequestviewset, basename='emp_leave-request')
router.register(r'emp-foreign', LeaveTypeViewSet, basename='emp_foreign')
router.register(r'attendance', AttendanceViewSet, basename='attendance')
router.register(r'shifts', ShiftViewSet,basename='shifts')
router.register(r'shiftpattern', ShiftPatternViewSet, basename='shiftpattern')
router.register(r'employee-shift', EmployeeShiftScheduleViewSet, basename='employee-shift')
router.register(r'shift-overrides', ShiftOverrideViewSet, basename='shift-overrides')
router.register(r'employee-leave-rejoins', EmployeeRejoiningViewset, basename='employee-leave-rejoins')

router.register(r'import-attendance', ImportAttendanceViewSet,basename='import-attendance')
router.register(r'employee-mapping', EmployeeMachineMappingViewset, basename='employee-mapping')
router.register(r'leave-report', Leave_ReportViewset, basename='leave-report')
router.register(r'leave-approval-levels', LvApprovalLevelViewset, basename='leave-approval-levels')
router.register(r'leave-approvals', LvApprovalViewset, basename='leave-approvals')
router.register(r'leave-template', LvEmailTemplateviewset, basename='leave-template')
router.register(r'leave-ApprovalNotify', LvApprovalNotifyviewset, basename='leave-ApprovalNotify')
router.register(r'leave-common-workflow', LvCommonWorkflowViewset, basename='leave-common-workflow')
router.register(r'leave-rejection-reason', LvRejectionViewset, basename='leave-rejection-reason')
router.register(r'Lv_Approval_Report', Lv_Approval_ReportViewset, basename='Lv_Approval_Report')
router.register(r'AttendanceReport', AttendanceReportViewset, basename='AttendanceReport')
router.register(r'lvBalanceReport', LvBalanceReportViewset, basename='lvBalanceReport')
router.register(r'EmployeeYearlyCalendarViewset',EmployeeYearlyCalendarViewset , basename='EmployeeYearlyCalendarViewset')
router.register(r'Emp-bulkupld-openings',EmpOpeningsBlkupldViewSet , basename='Emp-bulkupld-openings')
router.register(r'monthly-attendance', MonthlyAttendanceSummaryViewSet, basename='monthly-attendance')
router.register(r'Emp-overtime',EmployeeOvertimeViewset , basename='EmployeeOvertime')


urlpatterns = [
    # Other paths
    path('api/', include(router.urls)),
    path('api/leave-balance/apply-openings/', ApplyOpeningsAPIView.as_view(), name='apply-openings'),
    path('api/immediate-reject/', ImmediateRejectAPIView.as_view(), name='immediate-reject'),
    path('api/employee-attendance-summary/', EmployeeAttendanceSummaryAPIView.as_view(), name='employee-attendance-summary'),
    path("api/test-monthwise-accrual/", MonthwiseAccrualSimulationView.as_view(), name="test-monthwise-accrual"),
    path('api/leave/reset-preview/', LeaveResetPreviewAPIView.as_view(), name='leave-reset-preview'),
   

   
]
