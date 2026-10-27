"""v1.61.0 – public API v1 (see ZeoAdmin.api_v1)."""
from django.urls import path

from . import api_v1 as A

urlpatterns = [
    path('', A.IndexView.as_view()),
    path('employees/', A.EmployeesView.as_view()),
    path('employees/<int:pk>/', A.EmployeesView.as_view()),
    path('branches/', A.OrgView.as_view(kind='branches')),
    path('departments/', A.OrgView.as_view(kind='departments')),
    path('designations/', A.OrgView.as_view(kind='designations')),
    path('leave-requests/', A.LeaveRequestsView.as_view()),
    path('leave-balances/', A.LeaveBalancesView.as_view()),
    path('attendance-days/', A.AttendanceDaysView.as_view()),
    path('attendance/punches/', A.PunchesView.as_view()),
    path('payslips/', A.PayslipsView.as_view()),
]
