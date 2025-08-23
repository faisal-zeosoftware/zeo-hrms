from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (SalaryComponentViewSet,EmployeeSalaryStructureViewSet,PayslipViewSet,PayrollRunViewSet,PayslipComponentViewSet,LoanTypeviewset,LoanApplicationviewset,LoanRepaymentviewset,LoanApprovalviewset,LoanApprovalLevelsviewset,
                    EmpBulkuploadSalaryStructureViewSet,PayslipConfirmedViewSet,SIFDataView,AdvanceSalaryRequestViewset,AdvanceSalaryApprovalViewSet,AdvanceCommonWorkflowViewSet,PayslipCommonWorkflowViewSet,PayslipApprovalViewSet,AirTicketPolicyViewSet,AirTicketAllocationViewSet,AirTicketRequestViewSet,
                    LoanEmailTemplateViewSet,LoanNotificationViewSet,AdvSalaryNotificationViewSet,AdvSalaryEmailTemplateViewSet,AirTicketRuleViewSet
                    
                    )


router = DefaultRouter()
router.register(r'salarycomponent', SalaryComponentViewSet)
router.register(r'employeesalary', EmployeeSalaryStructureViewSet)
router.register(r'PayrollRun', PayrollRunViewSet)
router.register(r'bulk-upload-salary', EmpBulkuploadSalaryStructureViewSet,basename='bulk-upload-salary')
router.register(r'payslip', PayslipViewSet,basename='payslip')
router.register(r'PayslipComponent', PayslipComponentViewSet)
router.register(r'PayslipConfirmedViewSet', PayslipConfirmedViewSet,basename='payslip_confirm')
router.register(r'loan-type', LoanTypeviewset, basename='loan-type')
router.register(r'loan-application', LoanApplicationviewset, basename='loan-application')
router.register(r'loan-repayment', LoanRepaymentviewset, basename='loan-repayment')
router.register(r'loan-approval-levels', LoanApprovalLevelsviewset, basename='loan-approval-levels')
router.register(r'loan-approval', LoanApprovalviewset, basename='loan-approval')
router.register(r'loan-email-template', LoanEmailTemplateViewSet,basename='loan-email-template')
router.register(r'loan-notification', LoanNotificationViewSet,basename='loan-notification')

router.register(r'advance-salary-request', AdvanceSalaryRequestViewset, basename='advance-salary-request')
router.register(r'approval-salaryrequest', AdvanceSalaryApprovalViewSet,basename='advance-salary-approval')
router.register(r'advance-salary-approval-levels', AdvanceCommonWorkflowViewSet,basename='advance-salary-approval-levels')
router.register(r'advance-salary-email-template', AdvSalaryEmailTemplateViewSet,basename='advance-salary-email-template')
router.register(r'advance-salary-notification', AdvSalaryNotificationViewSet,basename='advance-salary-approval-notification')
router.register(r'approval-payroll', PayslipApprovalViewSet,basename='approval-payroll')
router.register(r'payslip-approval-levels', PayslipCommonWorkflowViewSet,basename='payslip-approval-levels')
router.register(r'airticket-rule', AirTicketRuleViewSet,basename='airticket-rule')
router.register(r'airticket-allocation', AirTicketAllocationViewSet,basename='airticket-allocation')
router.register(r'airticket-policy', AirTicketPolicyViewSet,basename='airticket-policy')
router.register(r'airticket-request', AirTicketRequestViewSet,basename='airticket-request')

urlpatterns = [
    path('api/', include(router.urls)),
    path('sif-data/', SIFDataView.as_view(), name='sif-data'),
]