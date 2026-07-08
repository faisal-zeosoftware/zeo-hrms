from rest_framework import serializers
from .models import (weekend_calendar,assign_weekend,holiday_calendar,holiday,assign_holiday,WeekendDetail,leave_type,leave_entitlement,emp_leave_balance,leave_accrual_transaction,employee_leave_request,
                     applicablity_critirea,leave_reset_transaction,Attendance,Shift,ShiftPattern,EmployeeShiftSchedule,
                    ShiftOverride,EmployeeMachineMapping,LeaveReport,
                     LeaveApprovalLevels,LeaveApproval,LvApprovalNotify,LvEmailTemplate,LvCommonWorkflow,LvRejectionReason,LeaveApprovalReport,
                    AttendanceReport,lvBalanceReport,CompensatoryLeaveRequest,CompensatoryLeaveBalance,CompensatoryLeaveTransaction,EmployeeYearlyCalendar,LeaveResetPolicy,LeaveCarryForwardTransaction,
                    LeaveEncashmentTransaction,EmployeeRejoining,EmployeeOvertime,MonthlyAttendanceSummary,AttendanceRecheck,OvertimePolicy,OvertimeRule,AttendanceLog,AttendancePolicy,LeavePayRule,
                    LatinEarlyoutEmailTemplate,LateinEarlyRequestNotification,LateinEarlyoutRequest,LateinEarlyoutApprovalLevel,LateinEarlyoutRequest,LateinEarlyoutApproval,LVApprovalWorkflow,LatinEarlyApprovalWorkflow,
                    AttendanceCalendar,CompensatoryLeaveAllocation,AttendanceValidationPolicy,LateComingPolicy,EarlyExitPolicy

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
    holiday_list = HolidaySerializer(many=True, read_only=True)
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
class LeavePayRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeavePayRule
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

class LeaveResetPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveResetPolicy
        fields = '__all__'

    def to_representation(self, instance):
        rep = super().to_representation(instance)

        if instance.leave_type:
            rep['leave_type'] = instance.leave_type.name

        if instance.leave_entitlement:
            rep['leave_entitlement'] = str(instance.leave_entitlement)

        return rep
    
class LeaveEntitlementSerializer(serializers.ModelSerializer):
    reset_policy = LeaveResetPolicySerializer(required=False)

    class Meta:
        model = leave_entitlement
        fields = '__all__'

    def create(self, validated_data):
        reset_policy_data = validated_data.pop('reset_policy', None)

        departments = validated_data.pop('departments', [])
        branches = validated_data.pop('branches', [])
        designations = validated_data.pop('designations', [])
        categories = validated_data.pop('categories', [])

        entitlement = leave_entitlement.objects.create(**validated_data)

        entitlement.departments.set(departments)
        entitlement.branches.set(branches)
        entitlement.designations.set(designations)
        entitlement.categories.set(categories)

        if reset_policy_data:
            LeaveResetPolicy.objects.create(
                leave_entitlement=entitlement,
                leave_type=entitlement.leave_type,
                **reset_policy_data
            )

        return entitlement

    def update(self, instance, validated_data):
        reset_policy_data = validated_data.pop('reset_policy', None)

        departments = validated_data.pop('departments', None)
        branches = validated_data.pop('branches', None)
        designations = validated_data.pop('designations', None)
        categories = validated_data.pop('categories', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()

        if departments is not None:
            instance.departments.set(departments)

        if branches is not None:
            instance.branches.set(branches)

        if designations is not None:
            instance.designations.set(designations)

        if categories is not None:
            instance.categories.set(categories)

        if reset_policy_data:
            reset_policy, created = LeaveResetPolicy.objects.get_or_create(
                leave_entitlement=instance,
                defaults={
                    'leave_type': instance.leave_type
                }
            )

            for attr, value in reset_policy_data.items():
                setattr(reset_policy, attr, value)

            reset_policy.leave_type = instance.leave_type
            reset_policy.save()

        return instance

    def to_representation(self, instance):
        rep = super().to_representation(instance)

        if instance.leave_type:
            rep['leave_type_name'] = instance.leave_type.name

        try:
            rep['reset_policy'] = LeaveResetPolicySerializer(
                instance.reset_policy
            ).data
        except LeaveResetPolicy.DoesNotExist:
            rep['reset_policy'] = None

        return rep
    # class Meta:
    #     model = leave_entitlement
    #     fields = '__all__'
    # def to_representation(self, instance):
    #     rep = super(LeaveEntitlementSerializer, self).to_representation(instance)
    #     if instance.leave_type:  
    #         rep['leave_type'] = instance.leave_type.name
    #     return rep

class LeaveTypeSerializer(serializers.ModelSerializer):
    pay_rules = LeavePayRuleSerializer(many=True, read_only=True)
    entitlements = LeaveEntitlementSerializer(
        source='leave_entitlement_set',
        many=True,
        read_only=True
    )
    # leave_category_name = serializers.CharField(source='leave_category.name', read_only=True)

    class Meta:
        model = leave_type
        fields = '__all__'

class LeaveRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = employee_leave_request
        fields = '__all__'

    def to_representation(self, instance):
        rep = super().to_representation(instance)

        if instance.employee:
            rep['employee'] = instance.employee.emp_first_name

        if instance.leave_type:
            rep['leave_type'] = instance.leave_type.name

        return rep

    def validate(self, data):
        leave_type = data.get('leave_type')
        employee = data.get('employee')

        if not leave_type or not employee:
            raise serializers.ValidationError("Employee and Leave Type are required.")

        is_half_day = data.get('dis_half_day', False)
        half_day_period = data.get('half_day_period')

        # ---------------- LEAVE TYPE VALIDATION ----------------
        if leave_type.unit == 'hours':
            duration = data.get('leave_duration', 0)
            if duration > 8 or duration <= 0:
                raise serializers.ValidationError(
                    "For hourly leave types, duration must be between 0 and 8 hours."
                )

        if leave_type.unit == 'days' and is_half_day:
            if data.get('start_date') != data.get('end_date'):
                raise serializers.ValidationError(
                    "Half-day leave must be same start and end date."
                )
            if not half_day_period:
                raise serializers.ValidationError(
                    "Half-day period is required."
                )

        # ---------------- WORKFLOW CHECK (SAFE + CONSISTENT) ----------------
        workflow = LVApprovalWorkflow.objects.filter(
            request_type=leave_type,
            branch__in=[employee.emp_branch_id]
        ).prefetch_related('leave_levels').first()

        if not workflow:
            raise serializers.ValidationError({
                "leave_type": "Approval workflow not configured for this branch."
            })

        # ---------------- REPORTING MANAGER ----------------
        if workflow.approval_type == 'reporting_manager':
            if not employee.emp_reporting_manager:
                raise serializers.ValidationError({
                    "employee": "No reporting manager assigned."
                })

        # ---------------- MULTI APPROVAL ----------------
        if workflow.approval_type == 'multi_approval':
            first_level = workflow.leave_levels.order_by('level').first()

            if not first_level:
                raise serializers.ValidationError({
                    "approval": "Approval levels not configured."
                })

            if not first_level.approver:
                raise serializers.ValidationError({
                    "approver": f"No approver assigned for level {first_level.level}"
                })

        return data
    
class EmployeeLeaveBalanceSerializer(serializers.ModelSerializer):
    leave_type_name = serializers.CharField(source='leave_type.name', read_only=True)
    negative = serializers.BooleanField(source='leave_type.negative', read_only=True)
    allow_half_day = serializers.BooleanField(source='leave_type.allow_half_day', read_only=True)
    include_dashboard = serializers.BooleanField(source='leave_type.include_dashboard', read_only=True)
    include_holiday = serializers.BooleanField(source='leave_type.include_holiday', read_only=True)
    # leave_type = serializers.PrimaryKeyRelatedField(queryset=leave_type.objects.none())

    class Meta:
        model = emp_leave_balance
        # fields = '__all__'
        fields = [
            'id',
            'employee',
            'leave_type',
            'leave_type_name',
            'balance',
            'openings',
            'updated_at',
            'created_at',
            'created_by',
            # leave_type boolean fields
            'include_dashboard',
            'negative',
            'allow_half_day',
            'include_holiday',

        ]
    def to_representation(self, instance):
        rep = super(EmployeeLeaveBalanceSerializer, self).to_representation(instance)
        if instance.leave_type:  
            rep['leave_type'] = instance.leave_type.name
        if instance.employee:  
            rep['employee'] = instance.employee.emp_code
           
        return rep
class AttendanceLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceLog
        fields = ['id', 'attendance', 'log_type', 'timestamp', 'lat', 'lng', 'location', 'is_face_verified', 'verification_photo','auth_method']
class AttendanceSerializer(serializers.ModelSerializer):
    logs = AttendanceLogSerializer(many=True, read_only=True)
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


class LatinEarlyoutEmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LatinEarlyoutEmailTemplate
        fields = '__all__'

    def validate(self, attrs):
        template_type = attrs.get("template_type")

        # ✅ Handle partial updates safely
        if not template_type:
            return attrs

        queryset = LatinEarlyoutEmailTemplate.objects.filter(
            template_type=template_type
        )

        # ✅ Exclude current instance during update
        if self.instance:
            queryset = queryset.exclude(id=self.instance.id)

        # ✅ Duplicate check
        if queryset.exists():
            raise serializers.ValidationError({
                "template_type": f"{template_type} template already exists."
            })

        return attrs
    
class LateinEarlyRequestNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = LateinEarlyRequestNotification
        fields = '__all__'

class LateinEarlyoutRequestSerializer(serializers.ModelSerializer):

    class Meta:
        model = LateinEarlyoutRequest
        fields = '__all__'

    def validate(self, data):

        employee = data.get('employee')

        if not employee:
            raise serializers.ValidationError({
                "employee": "Employee is required."
            })

        # ================= SAFE BRANCH RESOLUTION =================
        branch_obj = employee.emp_branch_id or getattr(employee, "work_location", None)
        branch_id = getattr(branch_obj, "id", None)

        if not branch_id:
            raise serializers.ValidationError({
                "employee": "Employee branch is missing."
            })

        # ================= WORKFLOW LOOKUP =================
        workflow = LatinEarlyApprovalWorkflow.objects.filter(
            branch__id=branch_id 
        ).first()

        if not workflow:
            raise serializers.ValidationError({
                "approval": "Approval workflow not configured for this branch."
            })

        # ================= RULES =================

        if workflow.approval_type == 'multi_approval':

            if not workflow.lateinearlyout_levels.order_by('level').exists():
                raise serializers.ValidationError({
                    "approval": "Approval levels are not configured."
                })

        if workflow.approval_type == 'reporting_manager':

            if not getattr(employee, "emp_reporting_manager", None):
                raise serializers.ValidationError({
                    "employee": "Employee has no reporting manager."
                })

        return data

    def to_representation(self, instance):
        rep = super().to_representation(instance)

        if instance.employee:
            rep['employee'] = getattr(instance.employee, "emp_code", None)

        if instance.request_type:
            rep['request_type'] = instance.request_type

        return rep
    

class LateinEarlyoutApprovalLevelSerializer(serializers.ModelSerializer):
    role = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    level = serializers.IntegerField(required=False)
    class Meta:
        model = LateinEarlyoutApprovalLevel
        fields = ['level', 'role', 'approver']
    def to_representation(self, instance):
        rep = super(LateinEarlyoutApprovalLevelSerializer, self).to_representation(instance)
        if instance.approver:  
            rep['approver'] = instance.approver.username
            return rep


class LatinEarlyApprovalWorkflowSerializer(serializers.ModelSerializer):
    levels = LateinEarlyoutApprovalLevelSerializer(many=True,source='lateinearlyout_levels',required=False)
    

    class Meta:
        model = LatinEarlyApprovalWorkflow
        fields = '__all__'

    # ================= FIX 1: SAFE REPRESENTATION =================
    def to_representation(self, instance):
        rep = super().to_representation(instance)

        if instance.branch.exists():
            rep['branch'] = [b.branch_name for b in instance.branch.all()]
        else:
            rep['branch'] = []

        return rep

    # ================= FIX 2: VALIDATION =================
    def validate(self, data):

        branches = data.get('branch')
        instance = getattr(self, 'instance', None)

        if not branches:
            return data

        clean_branch_ids = []

        for b in branches:
            clean_branch_ids.append(b.id if hasattr(b, "id") else b)

        for branch_id in clean_branch_ids:

            qs = LatinEarlyApprovalWorkflow.objects.filter(
                branch__id=branch_id   # ✅ FIX HERE
            )

            if instance:
                qs = qs.exclude(id=instance.id)

            if qs.exists():
                raise serializers.ValidationError(
                    f"Workflow already exists for branch {branch_id}"
                )

        return data

    # ================= FIX 3: CREATE =================
    def create(self, validated_data):
        levels_data = validated_data.pop('levels', None) or validated_data.pop('lateinearlyout_levels', [])
        branches = validated_data.pop('branch', [])

        workflow = LatinEarlyApprovalWorkflow.objects.create(**validated_data)

        if branches:
            workflow.branch.set(branches)

        for level_data in levels_data:
            level_data.pop('workflow', None)

            # FIX: ensure safe defaults
            if not level_data.get('level'):
                raise serializers.ValidationError("Level is required")

            LateinEarlyoutApprovalLevel.objects.create(
                workflow=workflow,
                **level_data
            )

        return workflow

    # ================= FIX 4: UPDATE =================
    def update(self, instance, validated_data):

        levels_data = validated_data.pop('lateinearlyout_levels', None)
        branches = validated_data.pop('branch', None)

        # ================= BASIC UPDATE =================
        approval_type = validated_data.get('approval_type', instance.approval_type)

        instance.approval_type = approval_type
        instance.save()

        # ================= BRANCH UPDATE =================
        if branches is not None:
            instance.branch.set(branches)

        # ================= LEVEL LOGIC (FIXED) =================

        # ❌ ALWAYS CLEAR IF NOT MULTI APPROVAL
        if approval_type != "multi_approval":
            instance.lateinearlyout_levels.all().delete()
            return instance

        # ================= MULTI APPROVAL =================

        # only process if levels provided
        if levels_data is None:
            return instance

        # clear old levels
        instance.lateinearlyout_levels.all().delete()

        for level_data in levels_data:

            LateinEarlyoutApprovalLevel.objects.create(
                workflow=instance,
                role=level_data.get("role") or "",
                approver=level_data.get("approver"),
                level=level_data.get("level"),
                # escalate_to=level_data.get("escalate_to"),
                # escalate_after_days=level_data.get("escalate_after_days"),
                # escalate_after_hours=level_data.get("escalate_after_hours"),
                # escalate_after_minutes=level_data.get("escalate_after_minutes"),
            )

        return instance

class LateinEarlyoutApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = LateinEarlyoutApproval
        fields = '__all__'

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
    def to_representation(self, instance):
        rep = super(ShiftOverrideSerializer, self).to_representation(instance)
        if instance.override_shift:  
            rep['override_shift'] = instance.override_shift.name
        if instance.employee:  
            rep['employee'] = instance.employee.emp_code
        return rep


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
    def validate(self, attrs):
        from EmpManagement.models import emp_master
        from django.db.models import Q
        from datetime import date

        start_date = attrs.get('start_date')
        end_date = attrs.get('end_date')

        if end_date and start_date > end_date:
            raise serializers.ValidationError(
                "End date must be greater than or equal to start date"
            )

        request = self.context['request']

        # M2M data from request
        employees = request.data.get('employee', [])
        branches = request.data.get('branches', [])
        departments = request.data.get('departments', [])
        designations = request.data.get('designations', [])
        categories = request.data.get('categories', [])

        # Resolve ALL affected employees
        affected_employees = emp_master.objects.none()

        if employees:
            affected_employees |= emp_master.objects.filter(id__in=employees)

        if branches:
            affected_employees |= emp_master.objects.filter(emp_branch_id__in=branches)

        if departments:
            affected_employees |= emp_master.objects.filter(emp_dept_id__in=departments)

        if designations:
            affected_employees |= emp_master.objects.filter(emp_desgntn_id__in=designations)

        if categories:
            affected_employees |= emp_master.objects.filter(emp_ctgry_id__in=categories)

        affected_employees = affected_employees.distinct()

        for emp in affected_employees:
            overlap = EmployeeShiftSchedule.objects.filter(
                employee=emp,
                start_date__lte=end_date or date.max
            ).filter(
                Q(end_date__gte=start_date) | Q(end_date__isnull=True)
            )

            if self.instance:
                overlap = overlap.exclude(id=self.instance.id)

            if overlap.exists():
                raise serializers.ValidationError(
                    f"Shift overlap detected for employee {emp.emp_code}"
                )

        return attrs
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
        workflow = attrs.get('workflow') or getattr(self.instance, 'workflow', None)

        qs = LeaveApprovalLevels.objects.all()

        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)

        if workflow and level is not None:
            if qs.filter(workflow=workflow, level=level).exists():
                raise serializers.ValidationError(
                    f"Level {level} already exists for this workflow."
                )

        return attrs

    def to_representation(self, instance):
        rep = super().to_representation(instance)

        if instance.approver:
            rep['approver'] = instance.approver.username

        if instance.escalate_to:
            rep['escalate_to'] = instance.escalate_to.username
        return rep
    
class LvApprovalSerializer(serializers.ModelSerializer):
    created_at = serializers.DateField(read_only=True)
    delegation_details = serializers.SerializerMethodField()

    class Meta:
        model = LeaveApproval
        fields = '__all__'

    def get_delegation_details(self, obj):
        return {
            "delegate_to_id": obj.deligate_to.id if obj.deligate_to else None,
            "delegate_to": obj.deligate_to.username if obj.deligate_to else None,
            "response": obj.deligate_response,
            "is_deligate": obj.is_deligate,
        }
    

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
        if instance.deligate_to:
                rep['deligate_to'] = instance.deligate_to.id if instance.deligate_to else None
        return rep
    
class LVApprovalWorkflowSerializer(serializers.ModelSerializer):

    levels = LvApprovalLevelSerializer(many=True,source='leave_levels',required=False)

    class Meta:
        model = LVApprovalWorkflow
        fields = '__all__'

    # ---------------- REPRESENTATION ---------------- #
    def to_representation(self, instance):

        rep = super().to_representation(instance)

        rep['levels'] = LvApprovalLevelSerializer(
            instance.leave_levels.all().order_by('level'),
            many=True,
            context=self.context
        ).data

        rep['request_type'] = (
            instance.request_type.name if instance.request_type else None
        )

        rep['branch'] = [
            b.branch_name for b in instance.branch.all()
        ] if instance.branch.exists() else []

        return rep

    # ---------------- CREATE ---------------- #
    def create(self, validated_data):

        # ✅ MUST match related_name / source
        levels_data = validated_data.pop('leave_levels', [])
        branches = validated_data.pop('branch', [])

        workflow = LVApprovalWorkflow.objects.create(**validated_data)

        if branches:
            workflow.branch.set(branches)

        # =========================================================
        # APPROVAL TYPE
        # =========================================================
        if workflow.approval_type == "reporting_manager":

            LeaveApprovalLevels.objects.create(
                workflow=workflow,
                level=1,
            )

        elif workflow.approval_type == "no_approval":

            LeaveApprovalLevels.objects.create(
                workflow=workflow,
                level=1,
            )

        else:

            for level_data in levels_data:
                LeaveApprovalLevels.objects.create(
                    workflow=workflow,
                    **level_data
                )

        return workflow

    # ---------------- UPDATE---------------- #
    def update(self, instance, validated_data):

        levels_data = validated_data.pop('levels', None)

        if levels_data is None:
            levels_data = validated_data.pop('leave_levels', None)

        branches = validated_data.pop('branch', None)

        instance.request_type = validated_data.get(
            'request_type',
            instance.request_type
        )

        instance.approval_type = validated_data.get(
            'approval_type',
            instance.approval_type
        )
        instance.save()

        # ---------------- BRANCH UPDATE ---------------- #
        if branches is not None:
            instance.branch.set(branches)
        instance.leave_levels.all().delete()

        # =========================================================
        # REPORTING MANAGER
        # =========================================================
        if instance.approval_type == "reporting_manager":

            LeaveApprovalLevels.objects.create(
                workflow=instance,
                level=1,
            )

            return instance

        # =========================================================
        # NO APPROVAL
        # =========================================================
        if instance.approval_type == "no_approval":

            LeaveApprovalLevels.objects.create(
                workflow=instance,
                level=1,
            )

            return instance

        # =========================================================
        # MULTI APPROVAL ONLY
        # =========================================================
        if levels_data is not None:

            for level_data in levels_data:

                # 🔥 FIX invalid pk issue (VERY IMPORTANT)
                if level_data.get("approver") in [0, "0", "", None]:
                    level_data["approver"] = None

                if level_data.get("escalate_to") in [0, "0", "", None]:
                    level_data["escalate_to"] = None

                level_data.pop('workflow', None)

                LeaveApprovalLevels.objects.create(
                    workflow=instance,
                    **level_data
                )

        return instance

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
    def to_representation(self, instance):
        rep = super(LvApprovalNotifySerializer, self).to_representation(instance)
        rep['recipient_user'] = instance.recipient_user.username if instance.recipient_user else None
        rep['recipient_employee'] = instance.recipient_employee.emp_first_name if instance.recipient_employee else None
        # rep['approval'] = instance.approval.id if instance.approval else None
        return rep

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

class CompensatoryLeaveAllocationSerializer(serializers.ModelSerializer):
    attendances = AttendanceSerializer(many=True, read_only=True)
    class Meta:
        model = CompensatoryLeaveAllocation
        fields ='__all__'

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
        if instance.approved_by:
            rep['approved_by'] = instance.approved_by.username    
        return rep

class LVEscalationRuleSerializer(serializers.ModelSerializer):
    request_type_name = serializers.CharField(
        source='workflow.request_type.name',
        read_only=True
    )

    approver_name = serializers.CharField(
        source='approver.username',
        read_only=True
    )

    escalate_to_name = serializers.CharField(
        source='escalate_to.username',
        read_only=True
    )
    branch = serializers.PrimaryKeyRelatedField(source='workflow.branch',many=True,read_only=True)

    class Meta:
        model = LeaveApprovalLevels
        fields = [
            'id',
            'level',
            'workflow',
            'request_type_name',
            'approver',
            'approver_name',
            'branch',
            'escalate_to',
            'escalate_to_name',
            'escalate_after_days',
            'escalate_after_hours',
            'escalate_after_minutes',
        ]

        read_only_fields = [
            'request_type_name',
            'approver_name',
            'escalate_to_name'
        ]
class AttendanceRecheckSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceRecheck
        fields = '__all__'
class OvertimePolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = OvertimePolicy
        fields = '__all__'
class OvertimeRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = OvertimeRule
        fields = '__all__'
class AttendancePolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendancePolicy
        fields = '__all__'
class AttendanceCalendarSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.emp_first_name', read_only=True)
    leave_type_name = serializers.CharField(source='leave_type.name', read_only=True)

    class Meta:
        model = AttendanceCalendar
        fields = '__all__'

class AttendanceValidationPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceValidationPolicy
        fields = '__all__'
class LateComingPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = LateComingPolicy
        fields = '__all__'
class EarlyExitPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = EarlyExitPolicy
        fields = '__all__'