from django.shortcuts import render
from .models import( weekend_calendar,assign_weekend,holiday,holiday_calendar,assign_holiday,WeekendDetail,leave_type,leave_entitlement,applicablity_critirea,emp_leave_balance,leave_accrual_transaction,leave_reset_transaction,employee_leave_request,Attendance,Shift,
                     EmployeeMachineMapping,LeaveReport,LeaveApprovalLevels,LeaveApproval,LvEmailTemplate,LvApprovalNotify,LvCommonWorkflow,LvRejectionReason,LeaveApprovalReport,
                     AttendanceReport,lvBalanceReport,EmployeeYearlyCalendar,CompensatoryLeaveRequest,CompensatoryLeaveTransaction,CompensatoryLeaveBalance,ShiftPattern,EmployeeShiftSchedule,ShiftOverride,LeaveResetPolicy,LeaveCarryForwardTransaction,
                    LeaveEncashmentTransaction,EmployeeRejoining,EmployeeOvertime,MonthlyAttendanceSummary
                    )
from . serializer import (WeekendCalendarSerailizer,WeekendAssignSerializer,HolidayAssignSerializer,HolidayCalandarSerializer,HolidaySerializer,WeekendDetailSerializer,LeaveTypeSerializer,LeaveEntitlementSerializer,ApplicableSerializer,EmployeeLeaveBalanceSerializer,AccrualSerializer,ResetSerializer,LeaveRequestSerializer,
                         AttendanceSerializer,ShiftSerializer,ImportAttendanceSerializer,EmployeeMappingSerializer,LeaveReportSerializer,LvApprovalLevelSerializer,EmployeeYearlyCalendarSerializer,
                         LvApprovalSerializer,LvEmailTemplateSerializer,LvApprovalNotifySerializer,LvCommonWorkflowSerializer,LvRejectionReasonSerializer,LvApprovalReportSerializer,AttendanceReportSerializer,lvBalanceReportSerializer,
                         CompensatoryLeaveRequestSerializer,CompensatoryLeaveTransactionSerializer,CompensatoryLeaveBalanceSerializer,ShiftOverrideSerializer,ShiftPatternSerializer,EmployeeShiftScheduleSerializer,LeaveResetPolicySerializer,LeaveCarryForwardTransactionSerializer,
                         LeaveEncashmentTransactionSerializer,EmpOpeningsBlkupldSerializer,EmployeeRejoiningSerializer,EmployeeOvertimeSerializer,MonthlyAttendanceSummarySerializer
                         )
from rest_framework import viewsets,filters,status
from rest_framework.response import Response
from rest_framework.decorators import action

from EmpManagement.models import emp_master
from .permissions import( WeekendCalendarPermission, WeekendDetailPermission, AssignWeekendPermission, HolidayPermission, HolidayCalendarPermission, AssignHolidayPermission,LeaveTypePermission,LeaveEntitlementPermission,EmpLeaveBalancePermission,ApplicabilityCriteriaPermission,EmployeeLeaveRequestPermission,LvEmailTemplatePermission,
                            LvCommonWorkflowPermission,LvRejectionReasonPermission,LeaveApprovalLevelsPermission,EmployeeMachineMappingPermission,ShiftPermission,ShiftPatternPermission,AttendancePermission,CompensatoryLeaveRequestPermission,CompensatoryLeaveTransactionPermission,CompensatoryLeaveBalancePermission,CompensatoryLeaveRequestPermission,
                            LeaveReportPermission,LeaveApprovalReportPermission,AttendanceReportPermission,LvBalanceReportPermission,LeaveAccrualTransactionPermission,LeaveResetTransactionPermission,ShiftOverridePermission,WeekPatternAssignmentPermission,EmployeeShiftSchedulePermission,EmployeeYearlyCalendarPermission,
                        )
from rest_framework.parsers import MultiPartParser, FormParser
from EmpManagement.models import emp_master
from .resource import AttendanceResource,EmployeeOpenBalanceResource,MonthlyAttendanceResource
from django.http import HttpResponse,JsonResponse
from tablib import Dataset
from django.core.exceptions import ValidationError
from rest_framework.views import APIView
from rest_framework.decorators import action
from django.utils import timezone
from django.shortcuts import get_object_or_404
from EmpManagement .models import EmailConfiguration
from django.utils.timezone import localtime,now
from django.conf import settings
import os,json
from datetime import date
from collections import defaultdict
from django.core.cache import cache
import redis
from rest_framework import viewsets,filters, status
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime, timedelta
from django.db.models import Field
from django.db import transaction
from django.db.models import Q
from OrganisationManager.models import DocumentNumbering
from OrganisationManager.serializer import DocumentNumberingSerializer
from rest_framework.exceptions import NotFound
from import_export.formats.base_formats import XLSX
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from datetime import date
from dateutil.relativedelta import relativedelta
from .utils import get_attendance_summary
from .serializer import AttendanceSummarySerializer
from EmpManagement.models import emp_master
import calendar
from django.utils.dateparse import parse_date

# Create your views here.

class WeekendDetailsViewset(viewsets.ModelViewSet):
    queryset = WeekendDetail.objects.all()
    serializer_class = WeekendDetailSerializer
    permission_classes = [WeekendCalendarPermission]

class WeekendViewset(viewsets.ModelViewSet):
    queryset = weekend_calendar.objects.all()
    serializer_class = WeekendCalendarSerailizer
    permission_classes = [WeekendDetailPermission]

    @action(detail=True, methods=['get'])
    def details(self, request, pk=None):
        weekend_calendar = self.get_object()
        serializer = self.get_serializer(weekend_calendar)
        return Response(serializer.data)
    # @action(detail=False, methods=['post'])
    # @action(detail=False, methods=['post'])
    # def set_monthly_weekends(self, request):
    #     data = request.data
    #     calendar_code = data.get('calendar_code')
    #     year = data.get('year')
    #     weekday = data.get('weekday')
    #     day_type = data.get('day_type')
    #     week_of_month = data.get('week_of_month')

    #     weekend_calendar, created = weekend_calendar.objects.get_or_create(calendar_code=calendar_code, year=year)
    #     for month in range(1, 13):
    #         for week in range(1, 6):  # Assuming up to 5 weeks in a month
    #             if week == week_of_month:
    #                 WeekendDetail.objects.create(
    #                     weekend_calendar=weekend_calendar,
    #                     weekday=weekday,
    #                     day_type=day_type,
    #                     week_of_month=week
    #                 )
        
    #     serializer = self.get_serializer(weekend_calendar)
    #     return Response(serializer.data)


class AssignWeekendViewset(viewsets.ModelViewSet):
    queryset = assign_weekend.objects.all()
    serializer_class = WeekendAssignSerializer
    permission_classes = [AssignWeekendPermission]
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)

class HolidayViewset(viewsets.ModelViewSet):
    queryset = holiday.objects.all()
    serializer_class = HolidaySerializer
    permission_classes = [HolidayPermission]

class HolidayCalendarViewset(viewsets.ModelViewSet):
    queryset = holiday_calendar.objects.all()
    serializer_class = HolidayCalandarSerializer
    permission_classes = [HolidayCalendarPermission]

class HolidayAssignViewset(viewsets.ModelViewSet):
    queryset = assign_holiday.objects.all()
    serializer_class = HolidayAssignSerializer
    permission_classes = [AssignHolidayPermission]

#leave
# Create your views here.

class LeaveTypeviewset(viewsets.ModelViewSet):
    queryset = leave_type.objects.all()
    serializer_class = LeaveTypeSerializer
    # permission_classes = [LeaveTypePermission] 
    
class LvEmailTemplateviewset(viewsets.ModelViewSet):
    queryset = LvEmailTemplate.objects.all()
    serializer_class = LvEmailTemplateSerializer
    permission_classes = [LvEmailTemplatePermission] 
    
    @action(detail=False, methods=['get'], url_path='placeholders')
    def placeholder_list(self, request):
        placeholders = {
            'request': [
                '{{ doc_number }}',
                '{{ request_type }}',
                '{{ reason }}',
                # Add other request-related placeholders here
            ],
            'employee': [
                '{{ document_number }}',
                '{{ leave_type }}',
                '{{ reason }}',
                '{{ recipient_name }}',
                '{{ emp_first_name }}',
                '{{ emp_last_name }}',
                '{{ emp_gender }}',
                '{{ emp_date_of_birth }}',
                '{{ emp_personal_email }}',
                '{{ emp_company_email }}',
                '{{ emp_branch_name }}',
                '{{ emp_department_name }}',
                '{{ emp_designation_name }}'
            ]
        }
        return Response(placeholders)
    # Custom action to fetch the available From and To addresses
    @action(detail=False, methods=['get'], url_path='from-to-addresses')
    def from_to_list(self, request):
        # Fetch active email configurations for "From" addresses
        from_addresses = EmailConfiguration.objects.filter(is_active=True).values_list('email_host_user', flat=True)

        # Fetch employee emails for "To" addresses
        to_addresses = emp_master.objects.all().values_list('emp_personal_email', 'emp_company_email')

        to_list = []
        for emp_personal, emp_company in to_addresses:
            if emp_personal:
                to_list.append(emp_personal)
            if emp_company:
                to_list.append(emp_company)

        return Response({
            'from_addresses': from_addresses,
            'to_addresses': to_list
        })

class LvApprovalNotifyviewset(viewsets.ModelViewSet):
    queryset = LvApprovalNotify.objects.all()
    serializer_class = LvApprovalNotifySerializer
    def get_queryset(self):
        user = self.request.user

        return LvApprovalNotify.objects.filter(
            Q(recipient_user=user) | Q(recipient_employee__user=user)
        ).order_by('-created_at')  # Fetch only relevant notifications, sorted by latest

class LeaveEntitlementviewset(viewsets.ModelViewSet):
    queryset = leave_entitlement.objects.all()
    serializer_class = LeaveEntitlementSerializer
    # permission_classes = [LeaveEntitlementPermission]
    def perform_create(self, serializer):
        instance = serializer.save()
        self.process_accrual(instance)

    def perform_update(self, serializer):
        instance = serializer.save()
        self.process_accrual(instance)

    def process_accrual(self, instance):
        if instance.accrual and instance.accrual_month == timezone.now().strftime('%b') and instance.accrual_day == '1st':
            employees = emp_leave_balance.objects.filter(leave_type=instance.leave_type)
            for emp_balance in employees:
                leave_accrual_transaction.objects.create(
                    employee=emp_balance.employee,
                    leave_type=instance.leave_type,
                    accrual_date=timezone.now().date(),
                    amount=instance.accrual_rate
                )
                emp_balance.balance += instance.accrual_rate
                emp_balance.save()

class LeaveResetPolicyviewset(viewsets.ModelViewSet):
    queryset = LeaveResetPolicy.objects.all()
    serializer_class = LeaveResetPolicySerializer

class LeaveCarryForwardTransactionviewset(viewsets.ModelViewSet):
    queryset = LeaveCarryForwardTransaction.objects.all()
    serializer_class = LeaveCarryForwardTransactionSerializer

class LeaveEncashmentTransactionviewset(viewsets.ModelViewSet):
    queryset = LeaveEncashmentTransaction.objects.all()
    serializer_class = LeaveEncashmentTransactionSerializer

class Applicableviewset(viewsets.ModelViewSet):
    queryset = applicablity_critirea.objects.all()
    serializer_class = ApplicableSerializer
    permission_classes = [ApplicabilityCriteriaPermission] 



class leave_balance_viewset(viewsets.ModelViewSet):
    queryset = emp_leave_balance.objects.all()
    serializer_class = EmployeeLeaveBalanceSerializer
    # permission_classes = [EmpLeaveBalancePermission] 

class Acrualviewset(viewsets.ModelViewSet):
    queryset = leave_accrual_transaction.objects.all()
    serializer_class = AccrualSerializer
    # permission_classes = [LeaveAccrualTransactionPermission] 

class Resetviewset(viewsets.ModelViewSet):
    queryset = leave_reset_transaction.objects.all()
    serializer_class = ResetSerializer
    # permission_classes = [LeaveResetTransactionPermission] 

# class Enchashviewset(viewsets.ModelViewSet):
#     queryset = leave_encashment.objects.all()
#     serializer_class = EnchashSerializer


class LeaveRequestviewset(viewsets.ModelViewSet):
    queryset = employee_leave_request.objects.all()
    serializer_class = LeaveRequestSerializer
    permission_classes = [EmployeeLeaveRequestPermission]
    def get_queryset(self):
        # Filter queryset based on user access
        if self.request.user.is_ess:
            # Return only requests related to the ESS user's employee record
            return self.queryset.filter(employee__emp_code=self.request.user.username)
        return super().get_queryset()  # Non-ESS users can access as per their permissions
    @action(detail=False, methods=['get'], url_path='approved-leaves')
    def approved_leaves(self, request):
        approved_queryset = employee_leave_request.objects.filter(status='approved')
        serializer = self.get_serializer(approved_queryset, many=True)
        return Response(serializer.data)
    def perform_create(self, serializer):
        with transaction.atomic():
            employee = serializer.validated_data.get('employee')
            branch_id = employee.emp_branch_id.id
            leave_type = serializer.validated_data['leave_type']

            # Document numbering logic (same as you already have)
            try:
                doc_config = DocumentNumbering.objects.get(
                    branch_id=branch_id,
                    type='leave_request',
                )
            except DocumentNumbering.DoesNotExist:
                raise NotFound(f"No document numbering configuration found for branch {branch_id} and leave type {leave_type}.")

            document_number = serializer.validated_data.get('document_number') or doc_config.get_next_number()

            # Save leave request
            leave_request = serializer.save(document_number=document_number)

            # === Create First Approval ===
            if leave_type.use_common_workflow:
                first_level = LvCommonWorkflow.objects.order_by('level').first()
            else:
                first_level = LeaveApprovalLevels.objects.filter(
                    request_type=leave_type,
                    branch__id=branch_id
                ).order_by('level').first()

            if first_level:
                print("s")
                if not leave_request.approvals.filter(level=first_level.level).exists():
                    LeaveApproval.objects.create(
                        leave_request=leave_request,
                        approver=first_level.approver,
                        level=first_level.level,
                        status=LeaveApproval.PENDING,
                        employee_id=employee.id
                    )

                # Send notification to first approver
                notification = LvApprovalNotify.objects.create(
                    recipient_user=first_level.approver,
                    message=f"New request for approval: {leave_request.leave_type}, employee: {leave_request.employee}"
                )
                notification.send_email_notification('request_created', {
                    'leave_type': leave_request.leave_type,
                    'start_date': leave_request.start_date,
                    'end_date': leave_request.end_date,
                    'status': leave_request.status,
                    'document_number': leave_request.document_number,
                    'employee_name': leave_request.employee.emp_first_name,
                    'reason': leave_request.reason,
                    'emp_gender': leave_request.employee.emp_gender,
                    'emp_date_of_birth': leave_request.employee.emp_date_of_birth,
                    'emp_personal_email': leave_request.employee.emp_personal_email,
                    'emp_branch_name': leave_request.employee.emp_branch_id,
                    'emp_department_name': leave_request.employee.emp_dept_id,
                    'emp_designation_name': leave_request.employee.emp_desgntn_id,
                })
    # def perform_create(self, serializer):
    #     with transaction.atomic():
    #         employee = serializer.validated_data.get('employee')
    #         branch_id = employee.emp_branch_id.id  # Ensure this field exists
    #         leave_type = serializer.validated_data['leave_type']

    #         try:
    #             doc_config = DocumentNumbering.objects.get(
    #                 branch_id=branch_id,
    #                 type='leave_request',
    #             )
    #         except DocumentNumbering.DoesNotExist:
    #             raise NotFound(f"No document numbering configuration found for branch {branch_id} and leave type {leave_type}.")

    #         # Check if the user entered a document number manually
    #         document_number = serializer.validated_data.get('document_number')

    #         if document_number:
    #             # Validate the entered document number falls within the allowed date range
    #             current_date = timezone.now().date()
    #             if doc_config.start_date and doc_config.end_date:
    #                 if not (doc_config.start_date <= current_date <= doc_config.end_date):
    #                     raise ValidationError("The document number cannot be assigned outside the valid date range.")
    #         else:
    #             # Generate the document number automatically
    #             document_number = doc_config.get_next_number()

    #         serializer.save(document_number=document_number)
            


    @action(detail=False, methods=['get'], url_path='leave-request-history')
    def employee_leave_request(self, request):
        employee_id = request.query_params.get('employee_id')
        if not employee_id:
            return Response({'error': 'Employee ID is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        requests = employee_leave_request.objects.filter(employee_id=employee_id).order_by('-applied_on')
     
        # Manually serialize the fields you want
        history_data = []
        for request in requests:
            history_data.append({
                'start_date': request.start_date,
                'end_date': request.end_date,
                'leave_type': request.leave_type.name if request.leave_type else None,
                'reason': request.reason ,
                'status': request.status,
                'applied_on': request.applied_on,
                'number_of_days':request.number_of_days
            })

        return Response(history_data, status=status.HTTP_200_OK)
    
#     def get_serializer_class(self):
#         if self.request.method in ['POST', 'PUT']:
#             return EmployeeLeaveSerializer
#         return super().get_serializer_class()

#     def get_serializer_context(self):
#         context = super().get_serializer_context()
#         context['employee_id'] = self.request.data.get('employee_id', None)
#         return context

#filtering for using assigned models for employees
from rest_framework.response import Response
from rest_framework.decorators import action

class LeaveTypeViewSet(viewsets.ViewSet):
    @action(detail=False, methods=['get'])
    def available_leave_types(self, request):
        employee_id = request.query_params.get('employee_id')
        if not employee_id:
            return Response({"error": "employee_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            employee = emp_master.objects.get(id=employee_id)
        except emp_master.DoesNotExist:
            return Response({"error": "Employee not found"}, status=status.HTTP_404_NOT_FOUND)

        leave_types = leave_type.objects.filter(
            id__in=emp_leave_balance.objects.filter(employee=employee).values_list('leave_type_id', flat=True)
        )
        serializer = LeaveTypeSerializer(leave_types, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class EmployeeMachineMappingViewset(viewsets.ModelViewSet):
    queryset =EmployeeMachineMapping.objects.all()
    serializer_class = EmployeeMappingSerializer
    permission_classes = [EmployeeMachineMappingPermission] 
    

class ShiftViewSet(viewsets.ModelViewSet):
    queryset = Shift.objects.all()
    serializer_class = ShiftSerializer
    permission_classes = [ShiftPermission] 

    

class ShiftPatternViewSet(viewsets.ModelViewSet):
    queryset = ShiftPattern.objects.all()
    serializer_class = ShiftPatternSerializer
    permission_classes = [ShiftPatternPermission]

class ShiftOverrideViewSet(viewsets.ModelViewSet):
    queryset = ShiftOverride.objects.all()
    serializer_class = ShiftOverrideSerializer
    permission_classes = [ShiftOverridePermission]

class EmployeeShiftScheduleViewSet(viewsets.ModelViewSet):
    queryset = EmployeeShiftSchedule.objects.all()
    serializer_class = EmployeeShiftScheduleSerializer
    # permission_classes = [EmployeeShiftSchedulePermission]
    def get_shift_for_day(self, request, *args, **kwargs):
        """
        Get shift for a given employee and date.
        URL parameters should include employee_id and date (format: YYYY-MM-DD).
        """
        employee_id = request.query_params.get('employee')
        date_str = request.query_params.get('date')
        
        if employee_id and date_str:
            try:
                employee = emp_master.objects.get(id=employee_id)
                date = timezone.datetime.strptime(date_str, '%Y-%m-%d').date()
                schedule = self.get_object()  # Assume the schedule is retrieved by URL ID
                # Removed extra employee argument:
                shift = schedule.get_shift_for_date(date)
                
                if shift:
                    return Response({"shift": str(shift)}, status=status.HTTP_200_OK)
                else:
                    return Response({"error": "No shift found for the specified date"}, status=status.HTTP_404_NOT_FOUND)
            except emp_master.DoesNotExist:
                return Response({"error": "Employee not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response({"error": "Invalid parameters"}, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['get'])
    def get_shifts_for_year(self, request):
        """
        Get shifts for all employees in a given schedule for every day in a specified year.
        Supports optional month-wise pagination via `month` parameter.
        """
        schedule_id = request.query_params.get('schedule_id')
        year = request.query_params.get('year')
        month = request.query_params.get('month')  # Optional: Get shifts only for a specific month

        if not (schedule_id and year):
            return Response({"error": "Missing required parameters"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            schedule = get_object_or_404(EmployeeShiftSchedule, id=schedule_id)
            year = int(year)

            # Determine date range (full year or specific month)
            if month:
                month = int(month)
                start_date = datetime(year, month, 1).date()
                end_date = (datetime(year, month + 1, 1) - timedelta(days=1)).date() if month < 12 else datetime(year, 12, 31).date()
            else:
                start_date = datetime(year, 1, 1).date()
                end_date = datetime(year, 12, 31).date()

            # **Efficiently fetch all assigned employees**
            assigned_employees = set(schedule.employee.all())  # Direct employees
            assigned_departments = set(schedule.departments.all())  # Departments assigned to schedule
            
            # Fetch employees in assigned departments
            department_employees = emp_master.objects.filter(emp_dept_id__in=assigned_departments)

            # Merge both sets to get all employees under this schedule
            all_valid_employees = list(assigned_employees.union(set(department_employees)))

            if not all_valid_employees:
                return Response({"error": "No employees assigned to this schedule"}, status=status.HTTP_404_NOT_FOUND)

            # **Precompute shifts in bulk**
            shifts_calendar = {}

            all_dates = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
            
            for employee in all_valid_employees:
                shifts_calendar[employee.emp_code] = {
                    date.strftime("%d-%m-%Y"): None for date in all_dates
                }

            # **Bulk fetch shifts for all dates**
            shift_data = {date: schedule.get_shift_for_date(date) for date in all_dates}

            # Assign shifts to employees in bulk
            for employee in all_valid_employees:
                for date, shift in shift_data.items():
                    date_str = date.strftime("%d-%m-%Y")
                    shifts_calendar[employee.emp_code][date_str] = str(shift) if shift else "No shift"

            return Response({
                "year": year,
                "month": month if month else "Full Year",
                "shifts": shifts_calendar
            }, status=status.HTTP_200_OK)

        except ValueError:
            return Response({"error": "Invalid year or month format"}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
class AttendanceViewSet(viewsets.ModelViewSet):
    queryset = Attendance.objects.all()
    serializer_class = AttendanceSerializer
    permission_classes = [AttendancePermission]
    
    @staticmethod
    def parse_date(date_string):
        try:
            # Parse the date from string to a datetime.date object
            return datetime.strptime(date_string, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
    @action(detail=False, methods=['post'])
    def check_in(self, request):
        emp_id = request.data.get("employee")
        date_str = request.data.get("date")
        date = self.parse_date(date_str) if date_str else timezone.now().date()

        try:
            employee = emp_master.objects.get(id=emp_id)
        except emp_master.DoesNotExist:
            return Response({"detail": "Employee not found"}, status=status.HTTP_404_NOT_FOUND)

        attendance, created = Attendance.objects.get_or_create(employee=employee, date=date)
        if attendance.check_in_time:
            return Response({"detail": "Already checked in"}, status=status.HTTP_400_BAD_REQUEST)

        #  Get shift schedules where the employee is directly assigned OR belongs to a department with an assigned shift
        schedule = EmployeeShiftSchedule.objects.filter(
            Q(employee=employee) | Q(departments=employee.emp_dept_id)
        ).first()  # Fetch the first matching schedule

        shift = schedule.get_shift_for_date(date) if schedule else None  #  Fixed this line

        # Get the current time in the tenant's timezone and store only the time
        tenant_time = localtime(now()).time()
        attendance.check_in_time = tenant_time
        attendance.shift = shift
        attendance.save()

        return Response(
            {"status": "Check-in recorded successfully", "shift": shift.name if shift else None},
            status=status.HTTP_200_OK
        )
    @action(detail=False, methods=['post'])
    def check_out(self, request):
        emp_id = request.data.get("employee")
        date_str = request.data.get("date")
        date = self.parse_date(date_str) if date_str else timezone.now().date()

        try:
            attendance = Attendance.objects.get(employee_id=emp_id, date=date)
        except Attendance.DoesNotExist:
            return Response({"detail": "No check-in record found"}, status=status.HTTP_400_BAD_REQUEST)

        if attendance.check_out_time:
            return Response({"detail": "Already checked out"}, status=status.HTTP_400_BAD_REQUEST)

        # Get the current time in the tenant's timezone and store only the time
        tenant_time = localtime(now()).time()  # Get the current time in the active timezone
        attendance.check_out_time = tenant_time
        attendance.calculate_total_hours()
        attendance.save()
        return Response({"status": "Check-out recorded successfully"}, status=status.HTTP_200_OK)
    @action(detail=False, methods=['get'])
    def monthly_late_and_early_attendance(self, request):
        from datetime import datetime, timedelta, datetime as dt

        month = request.query_params.get("month")
        year = request.query_params.get("year")

        if not month or not year:
            return Response({"detail": "month and year query parameters are required. Example: ?month=6&year=2025"}, status=400)

        try:
            month = int(month)
            year = int(year)
            start_date = datetime(year, month, 1).date()
            if month == 12:
                end_date = datetime(year + 1, 1, 1).date()
            else:
                end_date = datetime(year, month + 1, 1).date()
        except ValueError:
            return Response({"detail": "Invalid month or year."}, status=400)

        records = Attendance.objects.filter(
            date__gte=start_date,
            date__lt=end_date,
            check_in_time__isnull=False,
            check_out_time__isnull=False,
            shift__isnull=False
        )

        result = []

        for record in records:
            shift = record.shift
            check_in = record.check_in_time
            check_out = record.check_out_time
            shift_start = shift.start_time
            shift_end = shift.end_time
            break_duration = shift.break_duration or timedelta()

            # Skip if shift start or end time is missing
            if not shift_start or not shift_end:
                continue

            # Calculate expected working hours
            shift_start_dt = dt.combine(record.date, shift_start)
            shift_end_dt = dt.combine(record.date, shift_end)
            shift_work_duration = shift_end_dt - shift_start_dt - break_duration

            # Actual work duration
            check_in_dt = dt.combine(record.date, check_in)
            check_out_dt = dt.combine(record.date, check_out)
            actual_work_duration = check_out_dt - check_in_dt

            late_check_in = check_in > shift_start
            early_check_out = check_out < shift_end

            # Include if employee is late-in or early-out (or total hours < shift duration)
            if late_check_in or early_check_out or actual_work_duration < shift_work_duration:
                result.append({
                    "employee_id": record.employee.id,
                    "employee_name": f"{record.employee.emp_first_name} {record.employee.emp_last_name}",
                    "date": str(record.date),
                    "check_in_time": str(check_in),
                    "check_out_time": str(check_out),
                    "shift_start_time": str(shift_start),
                    "shift_end_time": str(shift_end),
                    "expected_work_duration": str(shift_work_duration),
                    "actual_work_duration": str(actual_work_duration),
                    "late_check_in": late_check_in,
                    "early_check_out": early_check_out,
                })

        return Response(result, status=200)
class ImportAttendanceViewSet(viewsets.ModelViewSet):
    queryset = Attendance.objects.all()
    serializer_class= ImportAttendanceSerializer
    permission_classes = [AttendancePermission]
    resource_class = AttendanceResource
    parser_classes = (MultiPartParser, FormParser)

    @action(detail=False, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def bulk_upload(self, request):
        if request.method == 'POST' and request.FILES.get('file'):
            excel_file = request.FILES['file']
            if excel_file.name.endswith('.xlsx'):
                try:
                    dataset = Dataset()
                    dataset.load(excel_file.read(), format='xlsx')
                    resource = AttendanceResource()
                    all_errors = []
                    valid_rows = []
                    
                    # Validate rows before import
                    for row_idx, row in enumerate(dataset.dict, start=2):
                        row_errors = []
                        try:
                            resource.before_import_row(row, row_idx=row_idx)
                        except ValidationError as e:
                            row_errors.extend([f"Row {row_idx}: {error}" for error in e.messages])
                        if row_errors:
                            all_errors.extend(row_errors)
                        else:
                            valid_rows.append(row)

                    if all_errors:
                        return Response({"errors": all_errors}, status=400)

                    # Import valid data and process shifts & total hours
                    result = resource.import_data(dataset, dry_run=False, raise_errors=True)

                    return Response({"message": f"{result.total_rows} attendances are added successfully"})
                except Exception as e:
                    return Response({"error": str(e)}, status=400)
            else:
                return Response({"error": "Invalid file format. Only Excel files (.xlsx) are supported."}, status=400)
        else:
            return Response({"error": "Please provide an Excel file."}, status=400)

class Leave_ReportViewset(viewsets.ModelViewSet):
    queryset = LeaveReport.objects.all()
    serializer_class = LeaveReportSerializer
    permission_classes = [LeaveReportPermission] 


    def __init__(self, *args, **kwargs):
        super(Leave_ReportViewset, self).__init__(*args, **kwargs)
        self.leave_standard_report_exists()
    def get_available_fields(self):
        excluded_fields = {'id', 'created_by','created_at'}
        included_emp_master_fields = { 'emp_first_name', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id','emp_branch_id'}
        
        display_names = {
            "employee": "Employee Code",
            "emp_first_name": "First Name",
            "emp_branch_id":"Branches",
            "emp_dept_id": "Department",
            "emp_desgntn_id": "Designation",
            "emp_ctgry_id": "Category",
            "leave_type": "Leave Type",
            "reason": "Reason",
            "status":"Status",
            "approved_by": "Approved Request",
            "applied_on":"Request Date",
           
        }

        emp_master_fields = [field.name for field in emp_master._meta.get_fields() if isinstance(field, Field) and field.name in included_emp_master_fields]
        leave_request_fields = [field.name for field in employee_leave_request._meta.get_fields() if isinstance(field, Field) and field.name not in excluded_fields]
        
        available_fields = {field: display_names.get(field, field) for field in emp_master_fields + leave_request_fields}
        return available_fields

    @action(detail=False, methods=['get'])
    def select_leavereport_fields(self, request, *args, **kwargs):
        available_fields = self.get_available_fields()
        return Response({'available_fields': available_fields})
       
    @action(detail=False, methods=['post'])
    def generate_leave_report(self, request, *args, **kwargs):
        if request.method == 'POST':
            try:
                file_name = request.POST.get('file_name', 'report')
                fields_to_include = request.POST.getlist('fields', [])
                # from_date = parse_date(request.POST.get('from_date'))
                # to_date = parse_date(request.POST.get('to_date'))
            except Exception as e:
                return JsonResponse({'status': 'error', 'message': str(e)})
            
            if not fields_to_include:
                fields_to_include = list(self.get_available_fields().keys())
            
            leavereport = employee_leave_request.objects.all()
            # documents = self.filter_documents_by_date_range(documents)

            report_data = self.generate_report_data(fields_to_include,leavereport)
            file_path = os.path.join(settings.MEDIA_ROOT, file_name + '.json')
            with open(file_path, 'w') as file:
                json.dump(report_data, file, default=str)  # Serialize dates to string format


            LeaveReport.objects.create(file_name=file_name, report_data=file_name + '.json')
            return JsonResponse({'status': 'success', 'file_path': file_path,'selected_fields_data': fields_to_include,})
        return JsonResponse({'status': 'error', 'message': 'Invalid request method'})

   
    def leave_standard_report_exists(self):
        # Update the standard report if it exists, otherwise create a new one
        if LeaveReport.objects.filter(file_name='leave_std_report').exists():
            self.generate_standard_report()
        else:
            self.generate_standard_report()
    
    def generate_standard_report(self):
        try:
            file_name = 'leave_std_report'
            fields_to_include = self.get_available_fields().keys()
            leavereport = employee_leave_request.objects.all()

            report_data = self.generate_report_data(fields_to_include, leavereport)
            file_path = os.path.join(settings.MEDIA_ROOT, file_name + '.json')

            # Save report data to a file
            with open(file_path, 'w') as file:
                json.dump(report_data, file, default=str)

            # Update or create the standard report entry in the database
            LeaveReport.objects.update_or_create(
                file_name=file_name,
                defaults={'report_data': file_name + '.json'}
            )

            print("Standard report generated successfully.")

        except Exception as e:
            print(f"Error generating standard report: {str(e)}")

    @action(detail=False, methods=['get'])
    def std_report(self, request, *args, **kwargs):
        try:
            # Ensure the standard report is up-to-date
            self.generate_standard_report()
            report = LeaveReport.objects.get(file_name='leave_std_report')
            serializer = self.get_serializer(report)
            return Response(serializer.data)
        except LeaveReport.DoesNotExist:
            return Response({"error": "Standard report not found."}, status=status.HTTP_404_NOT_FOUND)
    
    def generate_report_data(self, fields_to_include,generalreport):
        column_headings = {
            "employee": "Employee Code",
            "emp_first_name": "First Name",
            "emp_branch_id":"Branches",
            "emp_dept_id": "Department",
            "emp_desgntn_id": "Designation",
            "emp_ctgry_id": "Category",
            "leave_type": "Leave Type",
            "reason": "Reason",
            "status":"Status",
            "approved_by": "Approved Request",
            "applied_on":"Request Date",
        }

        emp_master_fields = [field.name for field in emp_master._meta.get_fields() if isinstance(field, Field) and field.name != 'id']
        leave_request_fields = [field.name for field in employee_leave_request._meta.get_fields() if isinstance(field, Field) and field.name != 'id']

        report_data = []
        for document in generalreport:
            general_data = {}
            for field in fields_to_include:
                if field in emp_master_fields:
                    value = getattr(document.employee, field, 'N/A')
                    if isinstance(value, date):
                        value = value.isoformat()
                elif field in leave_request_fields:
                    value = getattr(document, field, 'N/A')
                else:
                    value = 'N/A'
                general_data[field] = value
            report_data.append(general_data)
        return report_data
    
    @action(detail=False, methods=['get'])
    def select_filter_fields(self, request, *args, **kwargs):
        available_fields = self.get_available_fields()
        selected_fields = request.session.get('selected_fields', [])
        report_id = request.GET.get('report_id')  # Get report_id from query parameters

        
        return Response({
            'available_fields': available_fields,
            'selected_fields': selected_fields,
            'report_id': report_id
        })
    
    @action(detail=False, methods=['post'])
    def filter_by_date(self, request, *args, **kwargs):
        tenant_id = request.tenant.schema_name
        report_id = request.data.get('report_id')
        start_date = request.data.get('start_date')
        end_date = request.data.get('end_date')

        # Replace slashes with hyphens
        start_date = start_date.replace('/', '-')
        end_date = end_date.replace('/', '-')

        # Parse and validate the date range
        try:
            start_date = datetime.fromisoformat(start_date)
            end_date = datetime.fromisoformat(end_date)
        except ValueError as e:
            return JsonResponse({'status': 'error', 'message': f'Invalid date format: {str(e)}'}, status=400)

        # Fetch report data from your database
        try:
            report_instance = LeaveReport.objects.get(id=report_id)
            report_data = json.loads(report_instance.report_data.read().decode('utf-8'))
        except LeaveReport.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Report not found'}, status=404)

        # Filter data by date range
        date_filtered_data = [
            row for row in report_data
            if 'applied_on' in row and row['applied_on'] and
            start_date <= datetime.fromisoformat(row['applied_on']) <= end_date
        ]

        # Save filtered data to Redis cache
        cache_key = f"{tenant_id}_{report_id}_date_filtered_data"
        cache.set(cache_key, date_filtered_data, timeout=None)  # Set timeout as needed

        return JsonResponse({
            'date_filtered_data': date_filtered_data,
            'report_id': report_id,
        })
    
    @action(detail=False, methods=['post'])
    def generate_filter_table(self, request, *args, **kwargs):
        selected_fields = request.POST.getlist('selected_fields')
        report_id = request.data.get('report_id')

        # Save selected fields to session
        request.session['selected_fields'] = selected_fields

        # Fetch date-filtered report data from session
        date_filtered_data = request.session.get('date_filtered_data', [])
        print("previously date filtered ",date_filtered_data)
        
        # If no date-filtered data, attempt to fetch full report
        if not date_filtered_data:
            try:
                report = LeaveReport.objects.get(id=report_id)
                report_file_path = os.path.join(settings.MEDIA_ROOT, report.report_data.name)
                with open(report_file_path, 'r') as file:
                    report_content = json.load(file)
            except LeaveReport.DoesNotExist:
                return JsonResponse({'status': 'error', 'message': 'Report not found'})

            date_filtered_data = report_content

        # If no fields are selected for filtration, default to all existing fields in the report
        if not selected_fields:
            if date_filtered_data:
                selected_fields = list(date_filtered_data[0].keys())  # Default to all keys in the first record
            else:
                selected_fields = []  # No data in the report
            
        # Define display names for fields
        column_headings = {
            "employee": "Employee Code",
            "emp_first_name": "First Name",
            "emp_branch_id":"Branches",
            "emp_dept_id": "Department",
            "emp_desgntn_id": "Designation",
            "emp_ctgry_id": "Category",
            "leave_type": "Leave Type",
            "reason": "Reason",
            "status":"Status",
            "approved_by": "Approved Request",
            "applied_on":"Request Date",
        }

        # Get unique values for selected_fields from date-filtered data
        unique_values = self.get_unique_values_for_fields(date_filtered_data, selected_fields)

        processed_unique_values = {}
        for field, values in unique_values.items():
            processed_unique_values[field] = {
                'values': values,
            }

        return JsonResponse({
            'selected_fields': selected_fields,
            'report_id': report_id,
            'report_content': date_filtered_data,  # Pass filtered data to the frontend
            'unique_values': processed_unique_values,
            'column_headings':column_headings
        })
        

    def get_unique_values_for_fields(self, data, selected_fields):
        unique_values = {field: set() for field in selected_fields}
        # Extract data from the provided content
        for record in data:
            for field in selected_fields:
                if field in record:
                    unique_values[field].add(record[field])

        # Convert sets to lists
        for field in unique_values:
            unique_values[field] = list(unique_values[field])
        return unique_values
       
    
    @action(detail=False, methods=['post'])
    def general_filter_report(self, request, *args, **kwargs):
        tenant_id = request.tenant.schema_name
        report_id = request.data.get('report_id')

        # Retrieve filtered data from Redis cache
        cache_key = f"{tenant_id}_{report_id}_date_filtered_data"
        filtered_data = cache.get(cache_key)

        if filtered_data is None:
            return JsonResponse({'status': 'error', 'message': 'No date-filtered data available'}, status=404)
        # Apply additional filtering here if needed
        # For example, based on other fields:
        additional_filters = {key: value for key, value in request.data.items() if key not in ('report_id',)}
        
        # Further filter based on additional criteria
        filtered_data = [
            row for row in filtered_data
            if all(row.get(key) == value for key, value in additional_filters.items())
        ]
        return JsonResponse({
            'filtered_data': filtered_data,
            'report_id': report_id,
        })

    
class LvApprovalLevelViewset(viewsets.ModelViewSet):
    queryset=LeaveApprovalLevels.objects.all()
    serializer_class=LvApprovalLevelSerializer
    permission_classes = [LeaveApprovalLevelsPermission]


class LvCommonWorkflowViewset(viewsets.ModelViewSet):
    queryset=LvCommonWorkflow.objects.all()
    serializer_class=LvCommonWorkflowSerializer
    permission_classes = [LvCommonWorkflowPermission]

class LvRejectionViewset(viewsets.ModelViewSet):
    queryset=LvRejectionReason.objects.all()
    serializer_class=LvRejectionReasonSerializer
    permission_classes = [LvRejectionReasonPermission]

class LvApprovalViewset(viewsets.ModelViewSet):
    queryset=LeaveApproval.objects.all()
    serializer_class=LvApprovalSerializer
    lookup_field = 'pk'
    def get_queryset(self):
        """
        Filter approvals based on the authenticated user.
        """
        user = self.request.user  # Get the logged-in user
        if user.is_superuser:
            return LeaveApproval.objects.all()
        return LeaveApproval.objects.filter(approver=user)  # Filter approvals assigned to the user
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        approval = self.get_object()
        note = request.data.get('note')  # Get the note from the request
        approval.approve(note=note)
        return Response({'status': 'approved', 'note': note}, status=status.HTTP_200_OK)

    
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        approval = self.get_object()
        note = request.data.get('note')
        rejection_reason = request.data.get('rejection_reason')
        if not rejection_reason:
            raise ValidationError("Rejection reason is required.")
        # Just pass the text directly to the model method
        approval.reject(rejection_reason=rejection_reason, note=note)
        return Response(
            {
                'status': 'rejected',
                'note': note,
                'rejection_reason': rejection_reason
            },
            status=status.HTTP_200_OK
        )
    # Custom action to get grouped leave approvals
    @action(detail=False, methods=['get'])
    def grouped_approvals(self, request):
        approvals = LeaveApproval.objects.select_related('leave_request', 'compensatory_request', 'approver').order_by('leave_request', 'level')

        # Group approvals by leave_request or compensatory_request
        grouped_approvals = defaultdict(list)
        for approval in approvals:
            if approval.leave_request:
                request_id = f"LeaveRequest-{approval.leave_request.id}"
            elif approval.compensatory_request:
                request_id = f"CompensatoryRequest-{approval.compensatory_request.id}"
            else:
                # Skip approvals without any associated request
                continue

            grouped_approvals[request_id].append({
                'id': approval.id,
                'role': approval.role,
                'level': approval.level,
                'status': approval.status,
                'note': approval.note,
                'created_at': approval.created_at,
                'updated_at': approval.updated_at,
                'approver': approval.approver.username,
                'rejection_reason': approval.rejection_reason.reason_text if approval.rejection_reason else None,
            })

        # Format data to a list of dictionaries
        response_data = [
            {
                'request_id': request_id,
                'approvals': levels
            }
            for request_id, levels in grouped_approvals.items()
        ]

        return Response(response_data, status=status.HTTP_200_OK)
class Lv_Approval_ReportViewset(viewsets.ModelViewSet):
    queryset = LeaveApprovalReport.objects.all()
    serializer_class = LvApprovalReportSerializer
    # permission_classes = [LeaveApprovalReportPermission]
    
    def __init__(self, *args, **kwargs):
        super(Lv_Approval_ReportViewset, self).__init__(*args, **kwargs)
        self.lv_apprvl_std_report_exists()
    def get_available_fields(self):
        excluded_fields = {'id', 'created_by','created_at'}
        included_emp_master_fields = { 'emp_first_name', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id','emp_branch_id'}
        
        display_names = {
            "employee": "Employee Code",
            "emp_first_name": "First Name",
            "emp_branch_id":"Branches",
            "emp_dept_id": "Department",
            "emp_desgntn_id": "Designation",
            "emp_ctgry_id": "Category",
            "leave_request": "Leave Request",
            "approver":"Approver",
            "level":"Level",
            "created_at": "Approve/Reject Date",
            "status":"Status",
            "note": "Comments",
            "rejection_reason":"Rejection Reason",
           
        }

        emp_master_fields = [field.name for field in emp_master._meta.get_fields() if isinstance(field, Field) and field.name in included_emp_master_fields]
        leave_approval_fields = [field.name for field in LeaveApproval._meta.get_fields() if isinstance(field, Field) and field.name not in excluded_fields]
        
        available_fields = {field: display_names.get(field, field) for field in emp_master_fields + leave_approval_fields}
        return available_fields

    @action(detail=False, methods=['get'])
    def select_approve_report_fields(self, request, *args, **kwargs):
        available_fields = self.get_available_fields()
        return Response({'available_fields': available_fields})
       
    @action(detail=False, methods=['post'])
    def generate_leave_report(self, request, *args, **kwargs):
        if request.method == 'POST':
            try:
                file_name = request.POST.get('file_name', 'report')
                fields_to_include = request.POST.getlist('fields', [])
                # from_date = parse_date(request.POST.get('from_date'))
                # to_date = parse_date(request.POST.get('to_date'))
            except Exception as e:
                return JsonResponse({'status': 'error', 'message': str(e)})
            
            if not fields_to_include:
                fields_to_include = list(self.get_available_fields().keys())
            
            leavereport = LeaveApproval.objects.all()
            # documents = self.filter_documents_by_date_range(documents)

            report_data = self.generate_report_data(fields_to_include,leavereport)
            file_path = os.path.join(settings.MEDIA_ROOT, file_name + '.json')
            with open(file_path, 'w') as file:
                json.dump(report_data, file, default=str)  # Serialize dates to string format


            LeaveApprovalReport.objects.create(file_name=file_name, report_data=file_name + '.json')
            return JsonResponse({'status': 'success', 'file_path': file_path,'selected_fields_data': fields_to_include,})
        return JsonResponse({'status': 'error', 'message': 'Invalid request method'})

   
    def lv_apprvl_std_report_exists(self):
        # Update the standard report if it exists, otherwise create a new one
        if LeaveReport.objects.filter(file_name='lv_approv_std_report').exists():
            self.generate_standard_report()
        else:
            self.generate_standard_report()
    
    def generate_standard_report(self):
        try:
            file_name = 'lv_approv_std_report'
            fields_to_include = self.get_available_fields().keys()
            leavereport = LeaveApproval.objects.all()

            report_data = self.generate_report_data(fields_to_include, leavereport)
            file_path = os.path.join(settings.MEDIA_ROOT, file_name + '.json')

            # Save report data to a file
            with open(file_path, 'w') as file:
                json.dump(report_data, file, default=str)

            # Update or create the standard report entry in the database
            LeaveApprovalReport.objects.update_or_create(
                file_name=file_name,
                defaults={'report_data': file_name + '.json'}
            )

            print("Standard report generated successfully.")

        except Exception as e:
            print(f"Error generating standard report: {str(e)}")

    @action(detail=False, methods=['get'])
    def std_report(self, request, *args, **kwargs):
        try:
            # Ensure the standard report is up-to-date
            self.generate_standard_report()
            report = LeaveApprovalReport.objects.get(file_name='lv_approv_std_report')
            serializer = self.get_serializer(report)
            return Response(serializer.data)
        except LeaveApprovalReport.DoesNotExist:
            return Response({"error": "Standard report not found."}, status=status.HTTP_404_NOT_FOUND)
    def generate_report_data(self, fields_to_include, generalreport):
        emp_master_fields = [field.name for field in emp_master._meta.get_fields() if isinstance(field, Field) and field.name != 'id']
        leave_approval_fields = [field.name for field in LeaveApproval._meta.get_fields() if isinstance(field, Field) and field.name != 'id']

        report_data = {}

        for document in generalreport:
            leave_request = document.leave_request
            compensatory_request = document.compensatory_request
            request_id = leave_request.id if leave_request else (compensatory_request.id if compensatory_request else 'N/A')

            # Shared information container (one per request_id)
            if request_id not in report_data:
                shared_data = {
                    "request_id": request_id,
                    "approvals": []
                }

                employee = leave_request.employee if leave_request else (compensatory_request.employee if compensatory_request else None)

                for field in fields_to_include:
                    if field in emp_master_fields and employee:
                        shared_data[field] = getattr(employee, field, 'N/A')
                    elif field == 'leave_request' and leave_request:
                        shared_data['leave_request'] = str(leave_request)
                    elif field == 'compensatory_request' and compensatory_request:
                        shared_data['compensatory_request'] = str(compensatory_request)
                
                report_data[request_id] = shared_data

            # Approval-specific info
            approval_data = {}
            approver = document.approver if document.approver else None

            for field in fields_to_include:
                if field in leave_approval_fields:
                    approval_data[field] = getattr(document, field, 'N/A')
                elif field == 'approver' and approver:
                    approval_data['approver'] = approver.username
                elif field == 'rejection_reason' and document.rejection_reason:
                    approval_data['rejection_reason'] = document.rejection_reason.reason_text

            report_data[request_id]["approvals"].append(approval_data)

        return list(report_data.values())
    
    
    @action(detail=False, methods=['get'])
    def select_filter_fields(self, request, *args, **kwargs):
        available_fields = self.get_available_fields()
        selected_fields = request.session.get('selected_fields', [])
        report_id = request.GET.get('report_id')  # Get report_id from query parameters      
        return Response({
            'available_fields': available_fields,
            'selected_fields': selected_fields,
            'report_id': report_id
        })
    
    @action(detail=False, methods=['post'])
    def filter_by_date(self, request, *args, **kwargs):
        tenant_id = request.tenant.schema_name
        report_id = request.data.get('report_id')
        start_date = request.data.get('start_date')
        end_date = request.data.get('end_date')

        # Replace slashes with hyphens
        start_date = start_date.replace('/', '-')
        end_date = end_date.replace('/', '-')

        # Parse and validate the date range
        try:
            start_date = datetime.fromisoformat(start_date)
            end_date = datetime.fromisoformat(end_date)
        except ValueError as e:
            return JsonResponse({'status': 'error', 'message': f'Invalid date format: {str(e)}'}, status=400)

        # Fetch report data from your database
        try:
            report_instance = LeaveApprovalReport.objects.get(id=report_id)
            report_data = json.loads(report_instance.report_data.read().decode('utf-8'))
        except LeaveApprovalReport.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Report not found'}, status=404)

        # Filter data by date range
        date_filtered_data = [
            row for row in report_data
            if 'created_at' in row and row['created_at'] and
            start_date <= datetime.fromisoformat(row['created_at']) <= end_date
        ]

        # Save filtered data to Redis cache
        cache_key = f"{tenant_id}_{report_id}_date_filtered_data"
        cache.set(cache_key, date_filtered_data, timeout=None)  # Set timeout as needed

        return JsonResponse({
            'date_filtered_data': date_filtered_data,
            'report_id': report_id,
        })
    

    @action(detail=False, methods=['post'])
    def approval_filter_table(self, request, *args, **kwargs):
        selected_fields = request.POST.getlist('selected_fields')
        report_id = request.data.get('report_id')

        # Save selected fields to session
        request.session['selected_fields'] = selected_fields

        # Fetch date-filtered report data from session
        date_filtered_data = request.session.get('date_filtered_data', [])
        print("previosly date filtered ",date_filtered_data)
        
        # If no date-filtered data, attempt to fetch full report
        if not date_filtered_data:
            try:
                report = LeaveApprovalReport.objects.get(id=report_id)
                report_file_path = os.path.join(settings.MEDIA_ROOT, report.report_data.name)
                with open(report_file_path, 'r') as file:
                    report_content = json.load(file)
            except LeaveApprovalReport.DoesNotExist:
                return JsonResponse({'status': 'error', 'message': 'Report not found'})

            date_filtered_data = report_content

        # If no fields are selected for filtration, default to all existing fields in the report
        if not selected_fields:
            if date_filtered_data:
                selected_fields = list(date_filtered_data[0].keys())  # Default to all keys in the first record
            else:
                selected_fields = []  # No data in the report
        # Get unique values for selected_fields from date-filtered data
        unique_values = self.get_unique_values_for_fields(date_filtered_data, selected_fields)

        processed_unique_values = {}
        for field, values in unique_values.items():
            processed_unique_values[field] = {
                'values': values,
            }

        return JsonResponse({
            'selected_fields': selected_fields,
            'report_id': report_id,
            'report_content': date_filtered_data,  # Pass filtered data to the frontend
            'unique_values': processed_unique_values,
        })

    def get_unique_values_for_fields(self, data, selected_fields):
        unique_values = {field: set() for field in selected_fields}
        # Extract data from the provided content
        for record in data:
            for field in selected_fields:
                if field in record:
                    unique_values[field].add(record[field])

        # Convert sets to lists
        for field in unique_values:
            unique_values[field] = list(unique_values[field])
        return unique_values

    @action(detail=False, methods=['post'])
    def approval_filter_report(self, request, *args, **kwargs):
        tenant_id = request.tenant.schema_name
        report_id = request.data.get('report_id')

        # Retrieve filtered data from Redis cache
        cache_key = f"{tenant_id}_{report_id}_date_filtered_data"
        filtered_data = cache.get(cache_key)

        if filtered_data is None:
            return JsonResponse({'status': 'error', 'message': 'No date-filtered data available'}, status=404)

        # Apply additional filtering here if needed
        # For example, based on other fields:
        additional_filters = {key: value for key, value in request.data.items() if key not in ('report_id',)}
        
        # Further filter based on additional criteria
        filtered_data = [
            row for row in filtered_data
            if all(row.get(key) == value for key, value in additional_filters.items())
        ]

        return JsonResponse({
            'filtered_data': filtered_data,
            'report_id': report_id,
        })

class AttendanceReportViewset(viewsets.ModelViewSet):
    queryset = AttendanceReport.objects.all()
    serializer_class = AttendanceReportSerializer
    # permission_classes = [AttendanceReportPermission]

    
    
    def __init__(self, *args, **kwargs):
        super(AttendanceReportViewset, self).__init__(*args, **kwargs)
        self.attendance_standard_report_exists()
    def get_available_fields(self):
        excluded_fields = {'id', 'created_by'}
        included_emp_master_fields = { 'emp_first_name', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id'}
        
        display_names = {
            "employee": "Employee Code",
            "emp_first_name": "First Name",
            "emp_branch_id":"Branches",
            "emp_dept_id": "Department",
            "emp_desgntn_id": "Designation",
            "emp_ctgry_id": "Category",
            "shift": "Shift",
            "date": "Date",
            "check_in_time":"Check In",
            "check_out_time": "Check Out",
            "total_hours":"Total Hours",
           
        }

        emp_master_fields = [field.name for field in emp_master._meta.get_fields() if isinstance(field, Field) and field.name in included_emp_master_fields]
        attendance_fields = [field.name for field in Attendance._meta.get_fields() if isinstance(field, Field) and field.name not in excluded_fields]
        
        available_fields = {field: display_names.get(field, field) for field in emp_master_fields + attendance_fields}
        return available_fields

    @action(detail=False, methods=['get'])
    def select_attendancereport_fields(self, request, *args, **kwargs):
        available_fields = self.get_available_fields()
        return Response({'available_fields': available_fields})
       
    @action(detail=False, methods=['post'])
    def generate_leave_report(self, request, *args, **kwargs):
        if request.method == 'POST':
            try:
                file_name = request.POST.get('file_name', 'report')
                fields_to_include = request.POST.getlist('fields', [])
                # from_date = parse_date(request.POST.get('from_date'))
                # to_date = parse_date(request.POST.get('to_date'))
            except Exception as e:
                return JsonResponse({'status': 'error', 'message': str(e)})
            
            if not fields_to_include:
                fields_to_include = list(self.get_available_fields().keys())
            
            attendancereport = Attendance.objects.all()
            # documents = self.filter_documents_by_date_range(documents)

            report_data = self.generate_report_data(fields_to_include,attendancereport)
            file_path = os.path.join(settings.MEDIA_ROOT, file_name + '.json')
            with open(file_path, 'w') as file:
                json.dump(report_data, file, default=str)  # Serialize dates to string format


            AttendanceReport.objects.create(file_name=file_name, report_data=file_name + '.json')
            return JsonResponse({'status': 'success', 'file_path': file_path,'selected_fields_data': fields_to_include,})
        return JsonResponse({'status': 'error', 'message': 'Invalid request method'})

   
    def attendance_standard_report_exists(self):
        # Update the standard report if it exists, otherwise create a new one
        if AttendanceReport.objects.filter(file_name='attendance_std_report').exists():
            self.generate_standard_report()
        else:
            self.generate_standard_report()
    
    def generate_standard_report(self):
        try:
            file_name = 'attendance_std_report'
            fields_to_include = self.get_available_fields().keys()
            attendancereport = Attendance.objects.all()

            report_data = self.generate_report_data(fields_to_include, attendancereport)
            file_path = os.path.join(settings.MEDIA_ROOT, file_name + '.json')

            # Save report data to a file
            with open(file_path, 'w') as file:
                json.dump(report_data, file, default=str)

            # Update or create the standard report entry in the database
            AttendanceReport.objects.update_or_create(
                file_name=file_name,
                defaults={'report_data': file_name + '.json'}
            )

            print("Standard report generated successfully.")

        except Exception as e:
            print(f"Error generating standard report: {str(e)}")

    @action(detail=False, methods=['get'])
    def std_report(self, request, *args, **kwargs):
        try:
            # Ensure the standard report is up-to-date
            self.generate_standard_report()
            report = AttendanceReport.objects.get(file_name='attendance_std_report')
            serializer = self.get_serializer(report)
            return Response(serializer.data)
        except AttendanceReport.DoesNotExist:
            return Response({"error": "Standard report not found."}, status=status.HTTP_404_NOT_FOUND)
    
    def generate_report_data(self, fields_to_include,generalreport):
        emp_master_fields = [field.name for field in emp_master._meta.get_fields() if isinstance(field, Field) and field.name != 'id']
        leave_request_fields = [field.name for field in Attendance._meta.get_fields() if isinstance(field, Field) and field.name != 'id']

        report_data = []
        for document in generalreport:
            general_data = {}
            for field in fields_to_include:
                if field in emp_master_fields:
                    value = getattr(document.employee, field, 'N/A')
                    if isinstance(value, date):
                        value = value.isoformat()
                elif field in leave_request_fields:
                    value = getattr(document, field, 'N/A')
                else:
                    value = 'N/A'
                general_data[field] = value
            report_data.append(general_data)
        return report_data
   
    @action(detail=False, methods=['get'])
    def select_filter_fields(self, request, *args, **kwargs):
        available_fields = self.get_available_fields()
        selected_fields = request.session.get('selected_fields', [])
        report_id = request.GET.get('report_id')
        
        return Response({
            'available_fields': available_fields,
            'selected_fields': selected_fields,
            'report_id': report_id
        })
    
    @action(detail=False, methods=['post'])
    def filter_by_date(self, request, *args, **kwargs):
        tenant_id = request.tenant.schema_name
        report_id = request.data.get('report_id')
        start_date = request.data.get('start_date')
        end_date = request.data.get('end_date')

        # Replace slashes with hyphens
        start_date = start_date.replace('/', '-')
        end_date = end_date.replace('/', '-')

        # Parse and validate the date range
        try:
            start_date = datetime.fromisoformat(start_date)
            end_date = datetime.fromisoformat(end_date)
        except ValueError as e:
            return JsonResponse({'status': 'error', 'message': f'Invalid date format: {str(e)}'}, status=400)

        # Fetch report data from your database
        try:
            report_instance = AttendanceReport.objects.get(id=report_id)
            report_data = json.loads(report_instance.report_data.read().decode('utf-8'))
        except AttendanceReport.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Report not found'}, status=404)

        # Filter data by date range
        date_filtered_data = [
            row for row in report_data
            if 'date' in row and row['date'] and
            start_date <= datetime.fromisoformat(row['date']) <= end_date
        ]

        # Save filtered data to Redis cache
        cache_key = f"{tenant_id}_{report_id}_date_filtered_data"
        cache.set(cache_key, date_filtered_data, timeout=None)  # Set timeout as needed

        return JsonResponse({
            'date_filtered_data': date_filtered_data,
            'report_id': report_id,
        })
    

    @action(detail=False, methods=['post'])
    def attendance_filter_table(self, request, *args, **kwargs):
        selected_fields = request.POST.getlist('selected_fields')
        report_id = request.data.get('report_id')

        # Save selected fields to session
        request.session['selected_fields'] = selected_fields

        # Fetch date-filtered report data from session
        date_filtered_data = request.session.get('date_filtered_data', [])
        print("previosly date filtered ",date_filtered_data)
        
        # If no date-filtered data, attempt to fetch full report
        if not date_filtered_data:
            try:
                report = AttendanceReport.objects.get(id=report_id)
                report_file_path = os.path.join(settings.MEDIA_ROOT, report.report_data.name)
                with open(report_file_path, 'r') as file:
                    report_content = json.load(file)
            except AttendanceReport.DoesNotExist:
                return JsonResponse({'status': 'error', 'message': 'Report not found'})

            date_filtered_data = report_content

        # If no fields are selected for filtration, default to all existing fields in the report
        if not selected_fields:
            if date_filtered_data:
                selected_fields = list(date_filtered_data[0].keys())  # Default to all keys in the first record
            else:
                selected_fields = []  # No data in the report
        # Get unique values for selected_fields from date-filtered data
        unique_values = self.get_unique_values_for_fields(date_filtered_data, selected_fields)

        processed_unique_values = {}
        for field, values in unique_values.items():
            processed_unique_values[field] = {
                'values': values,
            }

        return JsonResponse({
            'selected_fields': selected_fields,
            'report_id': report_id,
            'report_content': date_filtered_data,  # Pass filtered data to the frontend
            'unique_values': processed_unique_values,
        })

    def get_unique_values_for_fields(self, data, selected_fields):
        unique_values = {field: set() for field in selected_fields}
        # Extract data from the provided content
        for record in data:
            for field in selected_fields:
                if field in record:
                    unique_values[field].add(record[field])

        # Convert sets to lists
        for field in unique_values:
            unique_values[field] = list(unique_values[field])
        return unique_values

    @action(detail=False, methods=['post'])
    def approval_filter_report(self, request, *args, **kwargs):
        tenant_id = request.tenant.schema_name
        report_id = request.data.get('report_id')

        # Retrieve filtered data from Redis cache
        cache_key = f"{tenant_id}_{report_id}_date_filtered_data"
        filtered_data = cache.get(cache_key)

        if filtered_data is None:
            return JsonResponse({'status': 'error', 'message': 'No date-filtered data available'}, status=404)

        # Apply additional filtering here if needed
        # For example, based on other fields:
        additional_filters = {key: value for key, value in request.data.items() if key not in ('report_id',)}
        
        # Further filter based on additional criteria
        filtered_data = [
            row for row in filtered_data
            if all(row.get(key) == value for key, value in additional_filters.items())
        ]

        return JsonResponse({
            'filtered_data': filtered_data,
            'report_id': report_id,
        })

class LvBalanceReportViewset(viewsets.ModelViewSet):
    queryset = lvBalanceReport.objects.all()
    serializer_class = lvBalanceReportSerializer
    # permission_classes = [LvBalanceReportPermission]

    

    def __init__(self, *args, **kwargs):
        super(LvBalanceReportViewset, self).__init__(*args, **kwargs)
        self.lvbalance_standard_report_exists()
    def get_available_fields(self):
        excluded_fields = {'id', 'created_by','created_at'}
        included_emp_master_fields = { 'emp_first_name', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id','emp_branch_id'}
        
        display_names = {
            "employee": "Employee Code",
            "emp_first_name": "First Name",
            "emp_branch_id":"Branches",
            "emp_dept_id": "Department",
            "emp_desgntn_id": "Designation",
            "emp_ctgry_id": "Category",
            "leave_type": "Leave Type",
            "balance": "Balance",
            "openings":"Openings",
            
           
        }

        emp_master_fields = [field.name for field in emp_master._meta.get_fields() if isinstance(field, Field) and field.name in included_emp_master_fields]
        leave_balance = [field.name for field in emp_leave_balance._meta.get_fields() if isinstance(field, Field) and field.name not in excluded_fields]
        
        available_fields = {field: display_names.get(field, field) for field in emp_master_fields + leave_balance}
        return available_fields

    @action(detail=False, methods=['get'])
    def select_attendancereport_fields(self, request, *args, **kwargs):
        available_fields = self.get_available_fields()
        return Response({'available_fields': available_fields})
       
    @action(detail=False, methods=['post'])
    def generate_leave_report(self, request, *args, **kwargs):
        if request.method == 'POST':
            try:
                file_name = request.POST.get('file_name', 'report')
                fields_to_include = request.POST.getlist('fields', [])
                # from_date = parse_date(request.POST.get('from_date'))
                # to_date = parse_date(request.POST.get('to_date'))
            except Exception as e:
                return JsonResponse({'status': 'error', 'message': str(e)})
            
            if not fields_to_include:
                fields_to_include = list(self.get_available_fields().keys())
            
            attendancereport = emp_leave_balance.objects.all()
            # documents = self.filter_documents_by_date_range(documents)

            report_data = self.generate_report_data(fields_to_include,attendancereport)
            file_path = os.path.join(settings.MEDIA_ROOT, file_name + '.json')
            with open(file_path, 'w') as file:
                json.dump(report_data, file, default=str)  # Serialize dates to string format


            lvBalanceReport.objects.create(file_name=file_name, report_data=file_name + '.json')
            return JsonResponse({'status': 'success', 'file_path': file_path,'selected_fields_data': fields_to_include,})
        return JsonResponse({'status': 'error', 'message': 'Invalid request method'})

   
    def lvbalance_standard_report_exists(self):
        # Update the standard report if it exists, otherwise create a new one
        if lvBalanceReport.objects.filter(file_name='lvbalance_std_report').exists():
            self.generate_standard_report()
        else:
            self.generate_standard_report()
    
    def generate_standard_report(self):
        try:
            file_name = 'lvbalance_std_report'
            fields_to_include = self.get_available_fields().keys()
            lvbalancereport = emp_leave_balance.objects.all()

            report_data = self.generate_report_data(fields_to_include, lvbalancereport)
            file_path = os.path.join(settings.MEDIA_ROOT, file_name + '.json')

            # Save report data to a file
            with open(file_path, 'w') as file:
                json.dump(report_data, file, default=str)

            # Update or create the standard report entry in the database
            lvBalanceReport.objects.update_or_create(
                file_name=file_name,
                defaults={'report_data': file_name + '.json'}
            )

            print("Standard report generated successfully.")

        except Exception as e:
            print(f"Error generating standard report: {str(e)}")

    @action(detail=False, methods=['get'])
    def std_report(self, request, *args, **kwargs):
        try:
            # Ensure the standard report is up-to-date
            self.generate_standard_report()
            report = lvBalanceReport.objects.get(file_name='lvbalance_std_report')
            serializer = self.get_serializer(report)
            return Response(serializer.data)
        except lvBalanceReport.DoesNotExist:
            return Response({"error": "Standard report not found."}, status=status.HTTP_404_NOT_FOUND)
    
    def generate_report_data(self, fields_to_include,generalreport):
        emp_master_fields = [field.name for field in emp_master._meta.get_fields() if isinstance(field, Field) and field.name != 'id']
        leave_request_fields = [field.name for field in emp_leave_balance._meta.get_fields() if isinstance(field, Field) and field.name != 'id']

        report_data = []
        for document in generalreport:
            general_data = {}
            for field in fields_to_include:
                if field in emp_master_fields:
                    value = getattr(document.employee, field, 'N/A')
                    if isinstance(value, date):
                        value = value.isoformat()
                elif field in leave_request_fields:
                    value = getattr(document, field, 'N/A')
                else:
                    value = 'N/A'
                general_data[field] = value
            report_data.append(general_data)
        return report_data
    
    @action(detail=False, methods=['get'])
    def select_filter_fields(self, request, *args, **kwargs):
       
        available_fields = self.get_available_fields()
        selected_fields = request.session.get('selected_fields', [])
        report_id = request.GET.get('report_id')

        return Response({
            'available_fields': available_fields,
            'selected_fields': selected_fields,
            'report_id': report_id
        })

    @csrf_exempt
    @action(detail=False, methods=['post'])
    def generate_balance_filter_table(self, request, *args, **kwargs):
        
        selected_fields = request.POST.getlist('selected_fields')
        report_id = request.POST.get('report_id')

        try:
            report = lvBalanceReport.objects.get(id=report_id)
            report_file_path = os.path.join(settings.MEDIA_ROOT, report.report_data.name)
            with open(report_file_path, 'r') as file:
                report_content = json.load(file)
        except lvBalanceReport.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Report not found'})

        if not selected_fields:
            selected_fields = list(report_content[0].keys()) if report_content else []

        balances = emp_leave_balance.objects.all()
        unique_values = self.get_unique_values_for_fields(balances, selected_fields, report_content)

        processed_unique_values = {
            field: {'values': values} for field, values in unique_values.items()
        }

        return JsonResponse({
            'selected_fields': selected_fields,
            'report_id': report_id,
            'report_content': report_content,
            'unique_values': processed_unique_values,
        })

    def get_unique_values_for_fields(self, balances, selected_fields, report_content):
        """
        Extract unique values for the selected fields.
        """
        unique_values = {field: set() for field in selected_fields}

        for record in report_content:
            for field in selected_fields:
                if field in record:
                    unique_values[field].add(record[field])

        for balance in balances:
            for field in selected_fields:
                if hasattr(balance, field):
                    value = getattr(balance, field, None)
                    if value is not None:
                        unique_values[field].add(value)

        return {field: list(values) for field, values in unique_values.items()}

    @csrf_exempt
    @action(detail=False, methods=['post'])
    def filter_existing_report(self, request, *args, **kwargs):
        
        report_id = request.data.get('report_id')
        if not report_id:
            return HttpResponse('Report ID is missing', status=400)

        try:
            report_instance = lvBalanceReport.objects.get(id=report_id)
            report_data = json.loads(report_instance.report_data.read().decode('utf-8'))
        except (lvBalanceReport.DoesNotExist, json.JSONDecodeError) as e:
            return HttpResponse(f'Report not found or invalid JSON: {str(e)}', status=404)

        selected_fields = [key for key in request.data.keys() if key != 'report_id']
        filter_criteria = {field: request.data.getlist(field) for field in selected_fields if request.data.getlist(field)}

        filtered_data = [
            row for row in report_data
            if self.match_filter_criteria(row, filter_criteria)
        ]

        request.session['filtered_data'] = filtered_data
        request.session.modified = True

        return JsonResponse({
            'filtered_data': filtered_data,
            'report_id': report_id,
        })

    def match_filter_criteria(self, row_data, filter_criteria):
        
        for field, values in filter_criteria.items():
            row_value = row_data.get(field)
            if row_value is None or str(row_value).strip() not in values:
                return False
        return True

class CompensatoryLeaveRequestviewset(viewsets.ModelViewSet):
    queryset = CompensatoryLeaveRequest.objects.all()
    serializer_class = CompensatoryLeaveRequestSerializer
    permission_classes = [CompensatoryLeaveRequestPermission]

class CompensatoryLeaveBalancetviewset(viewsets.ModelViewSet):
    queryset = CompensatoryLeaveBalance.objects.all()
    serializer_class = CompensatoryLeaveBalanceSerializer
    permission_classes = [CompensatoryLeaveBalancePermission]

class CompensatoryLeaveTransactionviewset(viewsets.ModelViewSet):
    queryset = CompensatoryLeaveTransaction.objects.all()
    serializer_class = CompensatoryLeaveTransactionSerializer 
    permission_classes = [CompensatoryLeaveTransactionPermission]

class EmployeeYearlyCalendarViewset(viewsets.ModelViewSet):
    queryset = EmployeeYearlyCalendar.objects.all()
    serializer_class = EmployeeYearlyCalendarSerializer
    permission_classes = [EmployeeYearlyCalendarPermission]

class EmpOpeningsBlkupldViewSet(viewsets.ModelViewSet):
    queryset = emp_leave_balance.objects.all()
    serializer_class = EmpOpeningsBlkupldSerializer
    
    @action(detail=False, methods=['post'])
    def bulk_upload(self, request):
        if 'file' not in request.FILES:
            return Response({"error": "Please provide an Excel file."}, status=400)

        excel_file = request.FILES['file']
        if not excel_file.name.endswith('.xlsx'):
            return Response({"error": "Invalid file format. Only .xlsx supported."}, status=400)

        try:
            xlsx_format = XLSX()
            dataset = xlsx_format.create_dataset(excel_file.read())

            resource = EmployeeOpenBalanceResource()
            all_errors = []

            with transaction.atomic():
                for row_idx, row in enumerate(dataset.dict, start=2):
                    try:
                        resource.before_import_row(row, row_idx=row_idx)
                        resource.import_row(row, None)
                    except ValidationError as e:
                        all_errors.extend([f"Row {row_idx}: {error}" for error in e.messages])

            if all_errors:
                return Response({"errors": all_errors}, status=400)

            return Response({"message": "Records updated successfully."})

        except Exception as e:
            return Response({"error": str(e)}, status=400)

class ApplyOpeningsAPIView(APIView):
    def post(self, request, *args, **kwargs):
        leave_balance_id = request.data.get('leave_balance_id')
        openings = request.data.get('openings')

        if leave_balance_id is None or openings is None:
            return Response({'error': 'Missing required fields.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            leave_balance = emp_leave_balance.objects.get(id=leave_balance_id)
        except emp_leave_balance.DoesNotExist:
            return Response({'error': 'Leave balance not found for given ID.'}, status=status.HTTP_404_NOT_FOUND)

        # Update the openings and balance
        leave_balance.openings = openings
        leave_balance.balance = (leave_balance.balance or 0) + openings  # in case balance is None
        leave_balance.save()

        return Response({'success': 'Openings applied successfully.'}, status=status.HTTP_200_OK)
class EmployeeRejoiningViewset(viewsets.ModelViewSet):
    queryset = EmployeeRejoining.objects.all()
    serializer_class = EmployeeRejoiningSerializer
    @action(detail=True, methods=['post'], url_path='deduct-leave-balance')
    def deduct_leave_balance(self, request, pk=None):
        """
        Custom action to choose from which leave type unpaid days should be deducted.
        """
        try:
            rejoining = self.get_object()
            deduct_from_leave_type = request.data.get('deduct_from_leave_type')

            if not deduct_from_leave_type:
                return Response({"error": "leave_type_id is required."}, status=status.HTTP_400_BAD_REQUEST)

            if rejoining.deducted:
                return Response({"error": "Leave balance already deducted for this rejoining."}, status=status.HTTP_400_BAD_REQUEST)

            # Get employee leave balance
            leave_balance, _ = emp_leave_balance.objects.get_or_create(
                employee=rejoining.employee,
                leave_type_id=deduct_from_leave_type
            )

            unpaid_days = rejoining.unpaid_leave_days

            if leave_balance.balance < unpaid_days:
                return Response({"error": "Not enough balance in the selected leave type."}, status=status.HTTP_400_BAD_REQUEST)

            # Deduct unpaid days
            old_balance = leave_balance.balance
            leave_balance.balance -= unpaid_days
            leave_balance.save()

            # Update rejoining record
            rejoining.deduct_from_leave_type_id = deduct_from_leave_type
            rejoining.deducted = True
            rejoining.save()

            return Response({
                "message": "Leave balance deducted successfully.",
                "employee": rejoining.employee.emp_first_name,
                "unpaid_days": unpaid_days,
                "old_balance": old_balance,
                "new_balance": leave_balance.balance
            }, status=status.HTTP_200_OK)

        except EmployeeRejoining.DoesNotExist:
            return Response({"error": "Rejoining record not found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
class ImmediateRejectAPIView(APIView):
    """
    API for users with special permissions to immediately reject an approved leave request by document number.
    """
    # permission_classes = [IsAuthenticated]  # Later you can add custom permission here

    def post(self, request, *args, **kwargs):
        document_number = request.data.get('document_number')
        rejection_reason = request.data.get('rejection_reason')
        note = request.data.get('note')

        if not document_number:
            return Response({'error': 'document_number is required.'}, status=status.HTTP_400_BAD_REQUEST)

        if not rejection_reason:
            return Response({'error': 'rejection_reason is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            leave_request = employee_leave_request.objects.get(document_number=document_number)
        except employee_leave_request.DoesNotExist:
            return Response({'error': 'Leave request not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Find the approved LeaveApproval for this leave request
        approval = LeaveApproval.objects.filter(
            leave_request=leave_request,
            status=LeaveApproval.APPROVED
        ).first()

        if not approval:
            return Response({'error': 'No approved leave request found for this document number.'}, status=status.HTTP_400_BAD_REQUEST)

        # # Optional: Check if user has permission to immediately reject
        # if not request.user.has_perm('yourapp.immediate_reject_leave'):
        #     raise PermissionDenied('You do not have permission to immediately reject leave requests.')

        # Perform rejection
        approval.status = LeaveApproval.REJECTED
        approval.rejection_reason = rejection_reason
        approval.note = note
        approval.save()

        # Also change the employee_leave_request status to REJECTED
        # leave_request.status = employee_leave_request.status='rejected'
        leave_request.status = 'rejected'
        leave_request.save()

        # Restore leave balance
        leave_request.restore_leave_balance()

        return Response(
            {
                'status': 'rejected_immediately',
                'document_number': document_number,
                'leave_approval_id': approval.id,
                'leave_request_id': leave_request.id
            },
            status=status.HTTP_200_OK
        )
class EmployeeAttendanceSummaryAPIView(APIView):
    def get(self, request):
        month = int(request.query_params.get("month", date.today().month))
        year = int(request.query_params.get("year", date.today().year))

        start_date = date(year, month, 1)
        end_date = start_date + relativedelta(months=1) - relativedelta(days=1)

        summaries = []

        # You can apply filters here if needed (e.g., by branch, department, etc.)
        all_employees = emp_master.objects.all()

        # for employee in all_employees:
        #     summary_data = get_attendance_summary(employee, start_date, end_date)
        #     serializer = AttendanceSummarySerializer(summary_data)

        #     # Include employee info with the summary
        #     summaries.append({
        #         # "employee_id": employee.id,
        #         "employee_name": f"{employee.emp_code}",
        #         "attendance_summary": serializer.data
        #     })
        #     return Response(serializer.data)
        for employee in all_employees:
            summary_data = get_attendance_summary(employee, start_date, end_date)
            
            if summary_data is None:
                continue  # or handle the case differently

            serializer = AttendanceSummarySerializer(summary_data)

            summaries.append({
                "employee_id": employee.id,
                "employee_name": f"{employee.emp_first_name}",
                "attendance_summary": serializer.data
            })
        return Response(summaries)

class MonthwiseAccrualSimulationView(APIView):
    def get(self, request, format=None):
        employee_id = request.query_params.get("employee_id")
        year = int(request.query_params.get("year", datetime.now().year))

        if not employee_id:
            return Response({"error": "employee_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            employee = emp_master.objects.get(id=employee_id)
        except emp_master.DoesNotExist:
            return Response({"error": "Employee not found"}, status=status.HTTP_404_NOT_FOUND)

        today = timezone.now().date()
        start_date = datetime(year, 1, 1).date()

        entitlements = leave_entitlement.objects.filter(accrual=True).order_by("leave_type", "min_experience")
        all_leave_types = leave_type.objects.all()

        # Select the best entitlement per leave type
        leave_type_entitlements = {}
        for entitlement in entitlements:
            lt = entitlement.leave_type

            base_date = (
                employee.emp_joined_date
                if entitlement.effective_after_from == "date_of_joining"
                else employee.emp_date_of_confirmation
            )
            if not base_date:
                continue

            experience = relativedelta(today, base_date)
            experience_months = experience.years * 12 + experience.months

            min_exp_months = (
                entitlement.min_experience * 12
                if entitlement.effective_after_unit == "years"
                else entitlement.min_experience
            )

            if experience_months >= min_exp_months and (
                lt not in leave_type_entitlements or
                min_exp_months > leave_type_entitlements[lt]["experience"]
            ):
                leave_type_entitlements[lt] = {
                    "entitlement": entitlement,
                    "experience": min_exp_months
                }

        accrual_summary = []

        for lt in all_leave_types:
            entitlement_info = leave_type_entitlements.get(lt)
            accrual_rate = 0
            frequency = None

            if entitlement_info:
                best_entitlement = entitlement_info["entitlement"]
                accrual_rate = best_entitlement.accrual_rate
                frequency = best_entitlement.accrual_frequency

            try:
                balance_obj = emp_leave_balance.objects.get(employee=employee, leave_type=lt)
                existing_balance = balance_obj.balance
            except emp_leave_balance.DoesNotExist:
                existing_balance = 0

            monthly_accruals = []
            accrued_sum = 0

            if frequency == "months" and accrual_rate > 0:
                for i in range(12):
                    accrual_month = start_date + relativedelta(months=i)
                    accrued_sum += accrual_rate
                    monthly_accruals.append({
                        "month": accrual_month.strftime("%b %Y"),
                        "accrued_balance": round(accrued_sum, 2),
                        "total_with_existing_balance": round(accrued_sum + existing_balance, 2)
                    })

            accrual_summary.append({
                "leave_type": lt.name,
                "accrual_frequency": frequency,
                "accrual_rate": accrual_rate,
                "existing_balance": round(existing_balance, 2),
                "monthly_accruals": monthly_accruals
            })

        response_data = {
            "employee_id": employee.id,
            "employee_name": f"{employee.emp_first_name} {employee.emp_last_name}",
            "year": year,
            "leave_accrual_summary": accrual_summary
        }

        return Response(response_data, status=status.HTTP_200_OK)
class LeaveResetPreviewAPIView(APIView):
    """
    API to preview leave reset logic without saving.
    """

    def post(self, request):
        # Accept custom reset_date or default to today
        reset_date_str = request.data.get("reset_date")
        if reset_date_str:
            try:
                reset_date = parse_date(reset_date_str)
                if not reset_date:
                    raise ValueError
            except ValueError:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({"error": "reset_date is required."}, status=status.HTTP_400_BAD_REQUEST)

        response_data = []

        resets = LeaveResetPolicy.objects.filter(reset=True)
        for reset in resets:
            reset_month = month_name_to_number(reset.month)
            reset_day = 1 if reset.day == "1st" else calendar.monthrange(reset_date.year, reset_month)[1]

            if reset.frequency == "years":
                if reset_date.month != reset_month or reset_date.day != reset_day:
                    continue
            elif reset.frequency == "months":
                if reset_date.day != reset_day:
                    continue

            leave_type = reset.leave_type
            employees = emp_master.objects.all()

            for emp in employees:
                leave_balance = emp_leave_balance.objects.filter(employee=emp, leave_type=leave_type).first()
                if not leave_balance:
                    continue

                initial_balance = leave_balance.balance if leave_balance.balance is not None else 0
                carry_forward_amount = 0
                encashment_amount = 0

                if reset.allow_cf and initial_balance > 0:
                    if reset.cf_unit_or_percentage == 'percentage':
                        calculated_cf = (initial_balance * reset.cf_value / 100)
                        carry_forward_amount = min(calculated_cf, reset.cf_max_limit or calculated_cf)
                    else:
                        carry_forward_amount = min(initial_balance, reset.cf_value)

                remaining_balance = initial_balance - carry_forward_amount

                if reset.allow_encashment and remaining_balance > 0:
                    if reset.encashment_unit_or_percentage == 'percentage':
                        encashment_amount = min((remaining_balance * reset.encashment_value / 100),
                                                reset.encashment_max_limit or remaining_balance)
                    else:
                        encashment_amount = min(remaining_balance, reset.encashment_value)

                response_data.append({
                    "employee": emp.emp_code,
                    "leave_type": leave_type.name,
                    "reset_date": reset_date,
                    "initial_balance": float(initial_balance),
                    "carry_forward": float(carry_forward_amount),
                    "encashment": float(encashment_amount),
                    "final_balance": float(carry_forward_amount),
                })

        return Response(response_data, status=status.HTTP_200_OK)


def month_name_to_number(month_name):
    month_map = {
        'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
        'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
    }
    return month_map.get(month_name[:3], datetime.now().month)

class EmployeeOvertimeViewset(viewsets.ModelViewSet):
    queryset = EmployeeOvertime.objects.all()
    serializer_class = EmployeeOvertimeSerializer

class MonthlyAttendanceSummaryViewSet(viewsets.ModelViewSet):
    queryset = MonthlyAttendanceSummary.objects.all()
    serializer_class = MonthlyAttendanceSummarySerializer
    @action(detail=False, methods=['post'])
    def generate(self, request):
        month = int(request.data.get("month", date.today().month))
        year = int(request.data.get("year", date.today().year))
        start_date = date(year, month, 1)
        end_date = start_date + relativedelta(months=1) - relativedelta(days=1)

        # Filters
        employee_ids = request.data.get("employee_id")  # list of IDs
        branch_id = request.data.get("branch_id")
        department_id = request.data.get("department_id")
        category_id = request.data.get("category_id")

        # Filter employees
        employees = emp_master.objects.all()

        if employee_ids:
            employees = employees.filter(id__in=employee_ids)

        if branch_id:
            employees = employees.filter(emp_branch_id=branch_id)

        if department_id:
            employees = employees.filter(emp_dept_id=department_id)

        if category_id:
            employees = employees.filter(emp_ctgry_id=category_id)

        result = []

        for employee in employees:
            summary_data = get_attendance_summary(employee, start_date, end_date)
            if not summary_data:
                continue

            serializer = AttendanceSummarySerializer(summary_data)

            summary_obj, _ = MonthlyAttendanceSummary.objects.update_or_create(
                employee=employee,
                month=month,
                year=year,
                defaults={
                    "summary_data": serializer.data["summary"],
                    "total_present": serializer.data["total_present"],
                    "total_absent": serializer.data["total_absent"],
                }
            )

            result.append(MonthlyAttendanceSummarySerializer(summary_obj).data)

        return Response(result)


class BulkuploadAttendanceViewSet(viewsets.ModelViewSet):
    queryset = Attendance.objects.all()
    serializer_class = ImportAttendanceSerializer
    parser_classes = (MultiPartParser, FormParser)

    @action(detail=False, methods=['post'])
    def bulk_upload(self, request):
        if 'file' not in request.FILES:
            return Response({"error": "Please provide an Excel file."}, status=400)

        excel_file = request.FILES['file']
        if not excel_file.name.endswith('.xlsx'):
            return Response({"error": "Invalid file format. Only .xlsx is supported."}, status=400)

        try:
            dataset = Dataset()
            dataset.load(excel_file.read(), format='xlsx')
            resource = MonthlyAttendanceResource()

            result = resource.import_data(dataset, dry_run=False, raise_errors=True)

            return Response({"message": "Monthly attendance imported successfully."})
        except Exception as e:
            return Response({"error": str(e)}, status=400)