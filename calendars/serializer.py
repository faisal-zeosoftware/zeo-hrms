from rest_framework import serializers
from .models import (weekend_calendar,assign_weekend,holiday_calendar,holiday,assign_holiday,WeekendDetail,leave_type,leave_entitlement,emp_leave_balance,leave_accrual_transaction,employee_leave_request,
                     applicablity_critirea,leave_reset_transaction,Attendance,Shift,ShiftPattern,EmployeeShiftSchedule,
                    ShiftOverride,EmployeeMachineMapping,LeaveReport,
                     LeaveApprovalLevels,LeaveApproval,LvApprovalNotify,LvEmailTemplate,LvCommonWorkflow,LvRejectionReason,LeaveApprovalReport,
                    AttendanceReport,lvBalanceReport,CompensatoryLeaveRequest,CompensatoryLeaveBalance,CompensatoryLeaveTransaction,EmployeeYearlyCalendar,LeaveResetPolicy,LeaveCarryForwardTransaction,
                    LeaveEncashmentTransaction,EmployeeRejoining,EmployeeOvertime,MonthlyAttendanceSummary

)
from OrganisationManager.serializer import BranchSerializer,CtgrySerializer,DeptSerializer
from OrganisationManager.models import brnch_mstr,dept_master,ctgry_master
from EmpManagement.models import emp_master
from rest_framework import serializers
from django.utils import timezone
from UserManagement .models import CustomUser
from OrganisationManager.serializer import DocumentNumberingSerializer
import json



class WeekendDetailSerializer(serializers.ModelSerializer):
    week_of_month = serializers.ChoiceField(choices=[(i, i) for i in range(1, 6)], required=False, allow_null=True, allow_blank=True)
    month_of_year = serializers.ChoiceField(choices=[(i, i) for i in range(1, 13)], required=False, allow_null=True, allow_blank=True)
    class Meta:
        model = WeekendDetail
        fields = '__all__'

class WeekendCalendarSerailizer(serializers.ModelSerializer):
    alternate_weekends = serializers.JSONField(required=False)
    year = serializers.ChoiceField(choices=[(year, year) for year in range(2000, 2040)])
    # details = WeekendDetailSerializer(many=True)
    details = WeekendDetailSerializer(many=True, read_only=True)
    class Meta:
        model = weekend_calendar
        fields = '__all__'
    def validate_alternate_weekends(self, value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except:
                raise serializers.ValidationError("Invalid JSON format for alternate_weekends")
        return value
        
    def validate(self, attrs):
        calendar_code=attrs.get("calendar_code")
        calendar=weekend_calendar.objects.filter(calendar_code=calendar_code)
        if self.instance:
            calendar=calendar.exclude(id=self.instance.id)
        if calendar.exists():
            raise serializers.ValidationError({f"{calendar_code} is already exists."
        })
        return attrs

class WeekendAssignSerializer(serializers.ModelSerializer):
    
    class Meta:
        model = assign_weekend
        fields = '__all__'
    
    def validate(self, data):
        related_to = data.get('related_to')
        weekend_model = data.get('weekend_model')
        
        if related_to == 'branch':
            existing_branches = data.get('branch')
            if existing_branches:
                existing = assign_weekend.objects.filter(
                    weekend_model=weekend_model,
                    branch__in=existing_branches
                ).exists()
                if existing:
                    raise serializers.ValidationError("The branch is already assigned to a weekend calendar.")
        
        elif related_to == 'department':
            existing_departments = data.get('department')
            if existing_departments:
                existing = assign_weekend.objects.filter(
                    weekend_model=weekend_model,
                    department__in=existing_departments
                ).exists()
                if existing:
                    raise serializers.ValidationError("The department is already assigned to a weekend calendar.")
        
        elif related_to == 'category':
            existing_categories = data.get('category')
            if existing_categories:
                existing = assign_weekend.objects.filter(
                    weekend_model=weekend_model,
                    category__in=existing_categories
                ).exists()
                if existing:
                    raise serializers.ValidationError("The category is already assigned to a weekend calendar.")
        
        elif related_to == 'employee':
            existing_employees = data.get('employee')
            if existing_employees:
                existing = assign_weekend.objects.filter(
                    weekend_model=weekend_model,
                    employee__in=existing_employees
                ).exists()
                if existing:
                    raise serializers.ValidationError("The employee is already assigned to a weekend calendar.")

        return data
    def to_representation(self, instance):
        rep = super(WeekendAssignSerializer, self).to_representation(instance)
        if instance.weekend_model:
            rep['weekend_model'] =instance.weekend_model.calendar_code
        # Handling Many-to-Many relationships correctly
        if instance.branch.exists():  # Ensure branch is not empty
            rep['branch'] = [branch.branch_name for branch in instance.branch.all()]
        
        if instance.category.exists():  # Ensure branch is not empty
            rep['category'] = [category.ctgry_title for category in instance.category.all()]
        
        if instance.department.exists():
            rep['department'] = [dept.dept_name for dept in instance.department.all()]
        if instance.designation.exists():
            rep['designation'] = [dept.desgntn_job_title for dept in instance.designation.all()]
        if instance.employee.exists():
            rep['employee'] = [emp.emp_code for emp in instance.employee.all()]
    
        return rep
class HolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = holiday
        fields = '__all__'

class HolidayCalandarSerializer(serializers.ModelSerializer):
    year = serializers.ChoiceField(choices=[(year, year) for year in range(2000, 2040)])
    holidays=HolidaySerializer(many=True,read_only=True,source="holiday_set")
    # holiday = HolidaySerializer(many=True,)
    class Meta:
        model = holiday_calendar
        fields = '__all__'
    def validate(self, attrs):
        calendar_title=attrs.get("calendar_title")
        calendar=holiday_calendar.objects.filter(calendar_title=calendar_title)
        if self.instance:
            calendar=calendar.exclude(id=self.instance.id)
        if calendar.exists():
            raise serializers.ValidationError({f"{calendar_title} is already exists."
        })
        return attrs

class HolidayAssignSerializer(serializers.ModelSerializer):
    class Meta:
        model = assign_holiday
        fields = '__all__'
    
    def to_representation(self, instance):
        rep = super(HolidayAssignSerializer, self).to_representation(instance)
        if instance.holiday_model:
            rep['holiday_model'] =instance.holiday_model.calendar_title
        # Handling Many-to-Many relationships correctly
        if instance.branch.exists():  # Ensure branch is not empty
            rep['branch'] = [branch.branch_name for branch in instance.branch.all()]
        
        if instance.category.exists():  # Ensure branch is not empty
            rep['category'] = [category.ctgry_title for category in instance.category.all()]
        
        if instance.department.exists():
            rep['department'] = [dept.dept_name for dept in instance.department.all()]
        
        if instance.employee.exists():
            rep['employee'] = [emp.emp_code for emp in instance.employee.all()]
    
        return rep
#leave
class LeaveTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = leave_type
        fields = '__all__'

class ApplicableSerializer(serializers.ModelSerializer):
    class Meta:
        model = applicablity_critirea
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(ApplicableSerializer, self).to_representation(instance)
        if instance.leave_type:  
            rep['leave_type'] = instance.leave_type.name
        rep['branch'] = [branch.branch_name for branch in instance.branch.all()]

        # Department names
        rep['department'] = [dept.dept_name for dept in instance.department.all()]

        # Designation names
        rep['designation'] = [desg.desgntn_job_title for desg in instance.designation.all()]

        # Role/Category names
        rep['role'] = [role.ctgry_title for role in instance.role.all()]
        return rep
class AccrualSerializer(serializers.ModelSerializer):
    class Meta:
        model = leave_accrual_transaction
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(AccrualSerializer, self).to_representation(instance)
        if instance.employee:  
            rep['employee'] = instance.employee.emp_first_name
        if instance.leave_type:  
            rep['leave_type'] = instance.leave_type.name
        return rep

class ResetSerializer(serializers.ModelSerializer):
    class Meta:
        model = leave_reset_transaction
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(ResetSerializer, self).to_representation(instance)
        if instance.employee:  
            rep['employee'] = instance.employee.emp_first_name
        if instance.leave_type:  
            rep['leave_type'] = instance.leave_type.name
        return rep
class LeaveCarryForwardTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveCarryForwardTransaction
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(LeaveCarryForwardTransactionSerializer, self).to_representation(instance)
        if instance.employee:  
            rep['employee'] = instance.employee.emp_first_name
        if instance.leave_type:  
            rep['leave_type'] = instance.leave_type.name
        return rep
class LeaveEncashmentTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveEncashmentTransaction
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(LeaveEncashmentTransactionSerializer, self).to_representation(instance)
        if instance.employee:  
            rep['employee'] = instance.employee.emp_first_name
        if instance.leave_type:  
            rep['leave_type'] = instance.leave_type.name
        return rep
class LeaveEntitlementSerializer(serializers.ModelSerializer):
    class Meta:
        model = leave_entitlement
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(LeaveEntitlementSerializer, self).to_representation(instance)
        if instance.leave_type:  
            rep['leave_type'] = instance.leave_type.name
        return rep

class LeaveResetPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveResetPolicy
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(LeaveResetPolicySerializer, self).to_representation(instance)
        if instance.leave_type:  
            rep['leave_type'] = instance.leave_type.name
        return rep
class LeaveRequestSerializer(serializers.ModelSerializer):
    # document_numbering_details = serializers.SerializerMethodField()
    class Meta:
        model = employee_leave_request
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(LeaveRequestSerializer, self).to_representation(instance)
        if instance.employee:  
            rep['employee'] = instance.employee.emp_first_name
        if instance.leave_type:  
            rep['leave_type'] = instance.leave_type.name
        
        return rep
    def validate(self, data):
        leave_type = data['leave_type']
        is_half_day = data.get('is_half_day', False)
        half_day_period = data.get('half_day_period')

        # Validate leave duration based on the unit
        if leave_type.unit == 'hours' and (data['leave_duration'] > 8 or data['leave_duration'] <= 0):
            raise serializers.ValidationError("For hourly leave types, duration must be between 0 and 8 hours.")
        
        if leave_type.unit == 'days':
            if is_half_day:
                if data['start_date'] != data['end_date']:
                    raise serializers.ValidationError("For half-day leave, start date and end date must be the same.")
                if not half_day_period:
                    raise serializers.ValidationError("Please specify whether the half-day is in the first or second half.")
            # elif data['leave_duration'] != 1:
            #     raise serializers.ValidationError("For daily leave types, duration must be a full day (1 day).")
        
        return data

class EmployeeLeaveBalanceSerializer(serializers.ModelSerializer):
    # leave_type = serializers.PrimaryKeyRelatedField(queryset=leave_type.objects.none())

    class Meta:
        model = emp_leave_balance
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(EmployeeLeaveBalanceSerializer, self).to_representation(instance)
        if instance.leave_type:  
            rep['leave_type'] = instance.leave_type.name
        if instance.employee:  
            rep['employee'] = instance.employee.emp_code
           
        return rep

class AttendanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attendance
        fields ='__all__'
    def to_representation(self, instance):
        rep = super(AttendanceSerializer, self).to_representation(instance)
        if instance.shift:  
            rep['shift'] = instance.shift.name
        if instance.employee:  
            rep['employee'] = instance.employee.emp_code
        return rep
    def update(self, instance, validated_data):
        # Detect if the date has been updated
        new_date = validated_data.get('date', instance.date)
        if new_date != instance.date:
            # Recalculate the shift if the date is updated
            schedule = EmployeeShiftSchedule.objects.filter(employee=instance.employee).first()
            if schedule:
                new_shift = schedule.get_shift_for_date(instance.employee, new_date)
                validated_data['shift'] = new_shift
        else:
            # If the date is not updated, retain the current shift
            validated_data['shift'] = instance.shift  # Keep the existing shift

        # Update fields
        instance.shift = validated_data.get('shift', instance.shift)
        instance.date = new_date
        instance.check_in_time = validated_data.get('check_in_time', instance.check_in_time)
        instance.check_out_time = validated_data.get('check_out_time', instance.check_out_time)

        # Calculate total hours if check-out time is updated
        if instance.check_in_time and instance.check_out_time:
            instance.calculate_total_hours()
        instance.save()
        return instance
    
class ImportAttendanceSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)
    class Meta:
        model = Attendance
        fields ='__all__'

class ShiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shift
        fields = '__all__'
class ShiftPatternSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShiftPattern
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(ShiftPatternSerializer, self).to_representation(instance)
        if instance.monday_shift:  
            rep['monday_shift'] = instance.monday_shift.name
        if instance.tuesday_shift:  
            rep['tuesday_shift'] = instance.tuesday_shift.name
        if instance.wednesday_shift:  
            rep['wednesday_shift'] = instance.wednesday_shift.name
        if instance.thursday_shift:  
            rep['thursday_shift'] = instance.thursday_shift.name
        if instance.friday_shift:  
            rep['friday_shift'] = instance.friday_shift.name
        if instance.saturday_shift:  
            rep['saturday_shift'] = instance.saturday_shift.name
        if instance.sunday_shift:  
            rep['sunday_shift'] = instance.sunday_shift.name
        return rep
class ShiftOverrideSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShiftOverride
        fields = '__all__'

class EmployeeShiftScheduleSerializer(serializers.ModelSerializer):
    branch_names = serializers.SerializerMethodField(read_only=True)
    department_names = serializers.SerializerMethodField(read_only=True)
    designation_names = serializers.SerializerMethodField(read_only=True)
    category_names = serializers.SerializerMethodField(read_only=True)
    employee_names = serializers.SerializerMethodField(read_only=True)
    # week_patterns = ShiftPatternSerializer(many=True, read_only=True)
    start_date = serializers.DateField(default=timezone.now().date)
    class Meta:
        model = EmployeeShiftSchedule
        fields = '__all__'
    def perform_create(self, serializer):
        schedule = serializer.save(created_by=self.request.user)

        # collect employees using branch, dept, cateogry, designation
        employees = emp_master.objects.all()

        if schedule.branches.exists():
            employees = employees.filter(emp_branch_id__in=schedule.branches.all())

        if schedule.departments.exists():
            employees = employees.filter(emp_dept_id__in=schedule.departments.all())

        if schedule.designations.exists():
            employees = employees.filter(emp_desgntn_id__in=schedule.designations.all())

        if schedule.categories.exists():
            employees = employees.filter(emp_ctgry_id__in=schedule.categories.all())

        # assign employees automatically
        schedule.employee.add(*employees)

        schedule.save()
    def get_branch_names(self, obj):
        return list(obj.branches.values('id', 'branch_name'))

    def get_department_names(self, obj):
        return list(obj.departments.values('id', 'dept_name'))

    def get_designation_names(self, obj):
        return list(obj.designations.values('id', 'desgntn_job_title'))

    def get_category_names(self, obj):
        return list(obj.categories.values('id', 'ctgry_title'))

    def get_employee_names(self, obj):
        return list(obj.employee.values('id', 'emp_code', 'emp_first_name', 'emp_last_name'))
class EmployeeMappingSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeMachineMapping
        fields = '__all__'
class LeaveReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveReport
        fields = '__all__'

class LvApprovalLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveApprovalLevels
        fields = '__all__'
    def validate(self, attrs):
        level = attrs.get('level')
        request_type = attrs.get('request_type')
        branches = attrs.get('branch')  # This will be a list of branches

        for branch in branches:
            if LeaveApprovalLevels.objects.filter(
                level=level,
                request_type=request_type,
                branch=branch
            ).exists():
                raise serializers.ValidationError(
                    f"An approval level with level={level} already exists for branch '{branch}' and request type '{request_type}'."
                )

        return attrs
    def to_representation(self, instance):
        rep = super(LvApprovalLevelSerializer, self).to_representation(instance)
        if instance.request_type:  
            rep['request_type'] = instance.request_type.name
        if instance.approver:  
            rep['approver'] = instance.approver.username
        rep['branch'] = list(instance.branch.values_list('branch_name', flat=True))
        return rep
class LvApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveApproval
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(LvApprovalSerializer, self).to_representation(instance)
        if instance.approver:
            rep['approver'] = instance.approver.username   
        if instance.leave_request:
            rep['leave_request'] = instance.leave_request.document_number
        if instance.employee_id:
            try:
                emp = emp_master.objects.get(id=instance.employee_id)
                rep['employee_id'] = emp.emp_code
            except emp_master.DoesNotExist:
                rep['employee_id'] = None
        return rep
   

class LvEmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LvEmailTemplate
        fields = '__all__'
    def validate(self, attrs):
        template_type=attrs.get("template_type")
        temp=LvEmailTemplate.objects.filter(template_type=template_type)
        if self.instance:
            temp=temp.exclude(id=self.instance.id)
        if temp.exists():
            raise serializers.ValidationError({"template_name": f"{template_type} template already exists."
        })
        return attrs
class LvApprovalNotifySerializer(serializers.ModelSerializer):
    class Meta:
        model = LvApprovalNotify
        fields = '__all__'

class LvCommonWorkflowSerializer(serializers.ModelSerializer):
    class Meta:
        model = LvCommonWorkflow
        fields = '__all__'
class LvRejectionReasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = LvRejectionReason
        fields = '__all__'
class LvApprovalReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveApprovalReport
        fields = '__all__'

class AttendanceReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceReport
        fields = '__all__'
class lvBalanceReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = lvBalanceReport
        fields = '__all__'

class CompensatoryLeaveRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompensatoryLeaveRequest
        fields ='__all__'
    

class CompensatoryLeaveBalanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompensatoryLeaveBalance
        fields = '__all__'

class CompensatoryLeaveTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompensatoryLeaveTransaction
        fields = '__all__'

class EmployeeYearlyCalendarSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeYearlyCalendar
        fields = '__all__'

class EmpOpeningsBlkupldSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)
    class Meta:
        model = emp_leave_balance
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(EmpOpeningsBlkupldSerializer, self).to_representation(instance)
        if instance.leave_type:  
            rep['leave_type'] = instance.leave_type.name
        if instance.employee:  
            rep['employee'] = instance.employee.emp_code
           
        return rep

class EmployeeRejoiningSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeRejoining
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(EmployeeRejoiningSerializer, self).to_representation(instance)
        if instance.deduct_from_leave_type:  
            rep['deduct_from_leave_type'] = instance.deduct_from_leave_type.name
        if instance.employee:  
            rep['employee'] = instance.employee.emp_code     
        if instance.leave_request:    
            rep['leave_request'] = instance.leave_request.document_number   
        return rep
class DailyAttendanceSerializer(serializers.Serializer):
    date = serializers.DateField()
    status = serializers.CharField()
    leave_type = serializers.CharField(allow_null=True, required=False)

class AttendanceSummarySerializer(serializers.Serializer):
    summary = DailyAttendanceSerializer(many=True)
    total_present = serializers.IntegerField()
    total_absent = serializers.IntegerField()
class MonthlyAttendanceSummarySerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.emp_first_name', read_only=True)

    class Meta:
        model = MonthlyAttendanceSummary
        fields = [
            'id', 'employee', 'employee_name', 'month', 'year',
            'summary_data', 'total_present', 'total_absent'
        ]
class EmployeeOvertimeSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeOvertime
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(EmployeeOvertimeSerializer, self).to_representation(instance)
        if instance.employee:  
            rep['employee'] = instance.employee.emp_code           
        return rep