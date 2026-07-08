from rest_framework import serializers
from .models import (SalaryComponent,EmployeeSalaryStructure,PayrollRun,Payslip,PayslipComponent,LoanType,LoanApplication,
                    LoanRepayment,LoanApprovalLevels,LoanApproval,AdvanceSalaryRequest,AdvanceSalaryApproval,AdvanceCommonWorkflow,PayslipApproval,PayslipCommonWorkflow,AirTicketPolicy,AirTicketAllocation,AirTicketRequest,
                    LoanEmailTemplate,LoanNotification,AdvanceSalaryEmailTemplate,AdvanceSalaryNotification,AirTicketRule,AirticketApproval,AirticketEmailTemplate,AirticketWorkflow,PayStructure,PayslipLeave,AirticketApprovalWorkflow,AdvanceApprovalWorkflow,LoanApprovalWorkflow,PayslipApprovalWorkflow,
                    
                    )

import calendar
from EmpManagement .models import EmployeeBankDetail,emp_master
from decimal import Decimal
from UserManagement.models import CustomUser


class SalaryComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalaryComponent
        fields = '__all__'
class EmployeeSalaryStructureSerializer(serializers.ModelSerializer):
    # For readable output
    emp_code = serializers.SerializerMethodField(read_only=True)
    component_name = serializers.CharField(source='component.name', read_only=True)
    component_type = serializers.CharField(source='component.get_component_type_display', read_only=True)
    emp_name = serializers.SerializerMethodField()
    department = serializers.CharField(source='employee.emp_dept_id.dept_name', read_only=True)
    designation = serializers.CharField(source='employee.emp_desgntn_id.desgntn_job_title', read_only=True)
    # s_fixied = serializers.BooleanField(source='component.is_fixed', read_only=True)
    component_value_type = serializers.CharField(source='component.component_value_type',read_only=True)

    payroll_category = serializers.CharField(source='component.payroll_category',read_only=True)

    class Meta:
        model = EmployeeSalaryStructure
        # Include 'employee' and 'component' for input
        fields = [
            'id', 'employee', 'emp_code',
            'component', 'component_name', 'component_type','component_value_type',
            'payroll_category',
            'amount', 'is_active', 'date_created', 'date_updated','department','designation','emp_name'
        ]
        extra_kwargs = {
            'employee': {'write_only': False},  # Allow it in input and output
            'component': {'write_only': True}   # Hide component ID in output
        }

    def get_emp_code(self, obj):
        return obj.employee.emp_code
    def get_emp_name(self, obj):
        return f"{obj.employee.emp_first_name} {obj.employee.emp_last_name}"

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        # Replace employee ID with employee code in output
        rep['employee'] = rep.pop('emp_code')
        # Replace component ID with component name
        rep['component'] = rep.pop('component_name')
        return rep

class EmpBulkuploadSalaryStructureSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)
    class Meta:
        model = EmployeeSalaryStructure
        fields = '__all__'

class PayrollRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayrollRun
        fields = '__all__'
    def to_representation(self, instance):
        rep = super().to_representation(instance)

        rep['branch_name'] = instance.branch.branch_name if instance.branch else None
        rep['department_name'] = instance.department.dept_name if instance.department else None
        rep['category_name'] = instance.category.ctgry_title if instance.category else None

        return rep

    def validate(self, data):
        month = data.get('month')
        year = data.get('year')
        branch = data.get('branch')
        department = data.get('department')
        category = data.get('category')
        employees = data.get('employees', [])

        # ---------------- COLLECT EMPLOYEES ----------------
        employee_ids = set(emp.id for emp in employees)

        qs = emp_master.objects.filter(is_active=True)

        if branch:
            qs = qs.filter(emp_branch_id=branch.id)

        if department:
            qs = qs.filter(emp_dept_id=department.id)

        if category:
            qs = qs.filter(emp_ctgry_id=category.id)

        employee_ids.update(qs.values_list('id', flat=True))

        # ---------------- CHECK DUPLICATES ----------------
        existing_runs = PayrollRun.objects.filter(
            month=month,
            year=year,
            employees__id__in=employee_ids
        )

        if self.instance:
            existing_runs = existing_runs.exclude(pk=self.instance.pk)

        duplicate_ids = existing_runs.values_list('employees__id', flat=True).distinct()

        duplicate_emps = emp_master.objects.filter(
            id__in=duplicate_ids
        ).values_list('emp_code', flat=True)

        if duplicate_emps:
            raise serializers.ValidationError(
                f"Payroll already exists for employees in {month}/{year}: {', '.join(duplicate_emps)}"
            )

        return data
class PaySlipComponentSerializer(serializers.ModelSerializer):
    component_name = serializers.CharField(source='component.name', read_only=True)
    component_type = serializers.CharField(source='component.get_component_type_display', read_only=True)

    class Meta:
        model = PayslipComponent
        fields = ['id', 'component_name', 'component_type', 'amount']

class PayslipLeaveSerializer(serializers.ModelSerializer):
    leave_type_name = serializers.CharField(
        source="leave_type.leave_type", read_only=True
    )

    class Meta:
        model = PayslipLeave
        fields = ["leave_type", "leave_type_name", "days"]
class PayslipSerializer(serializers.ModelSerializer):
    currency_details = serializers.SerializerMethodField()
    payroll_run = PayrollRunSerializer(read_only=True)
    employee = serializers.StringRelatedField()
    components = serializers.SerializerMethodField()
    currency_details = serializers.SerializerMethodField()
    leave_details = PayslipLeaveSerializer(many=True, read_only=True)

    class Meta:
        model = Payslip
        fields = '__all__'
    def get_currency_details(self, obj):
        request = self.context.get("request")
        if request and hasattr(request, "tenant") and request.tenant.currency:
            currency = request.tenant.currency
            return {
                "currency_name": currency.currency_name,
                "currency_code": currency.currency_code,
                "symbol": currency.symbol
            }
        return None
    def get_components(self, obj):
        # Fetch PayslipComponent data
        payslip_components = PaySlipComponentSerializer(
            obj.components.all(), many=True
        ).data

        # Fetch EmployeeSalaryStructure data
        salary_structures = EmployeeSalaryStructureSerializer(
            obj.employee.salary_structures.filter(is_active=True), many=True
        ).data

        # Combine data into a single list
        combined = []
        component_names = set()

        # Process PayslipComponent entries
        for pc in payslip_components:
            combined.append({
                'id': pc['id'],
                'component_name': pc['component_name'],
                'component_type': pc['component_type'],
                'payslip_amount': pc['amount'],
                'structure_amount': None,
                'is_active': None,
                'date_created': None,
                'date_updated': None,
                'employee': str(obj.employee),
                'component': pc['component_name']
            })
            component_names.add(pc['component_name'])

        # Process EmployeeSalaryStructure entries
        for ss in salary_structures:
            if ss['component'] in component_names:
                # Update existing component with structure data
                for item in combined:
                    if item['component_name'] == ss['component']:
                        item['structure_amount'] = ss['amount']
                        item['is_active'] = ss['is_active']
                        item['date_created'] = ss['date_created']
                        item['date_updated'] = ss['date_updated']
                        break
            else:
                # Add new component from salary structure
                combined.append({
                    'id': ss['id'],
                    'component_name': ss['component'],
                    'component_type': ss['component_type'],
                    'payslip_amount': None,
                    'structure_amount': ss['amount'],
                    'is_active': ss['is_active'],
                    'date_created': ss['date_created'],
                    'date_updated': ss['date_updated'],
                    'employee': ss['employee'],
                    'component': ss['component']
                })

        return combined

class PayslipConfirmedSerializer(serializers.ModelSerializer):
    payroll_run = PayrollRunSerializer(read_only=True)
    employee = serializers.StringRelatedField()
    components = serializers.SerializerMethodField()

    class Meta:
        model = Payslip
        fields = '__all__'
    def get_components(self, obj):
        # Fetch PayslipComponent data
        payslip_components = PaySlipComponentSerializer(
            obj.components.all(), many=True
        ).data

        # Fetch EmployeeSalaryStructure data
        salary_structures = EmployeeSalaryStructureSerializer(
            obj.employee.salary_structures.filter(is_active=True), many=True
        ).data

        # Combine data into a single list
        combined = []
        component_names = set()

        # Process PayslipComponent entries
        for pc in payslip_components:
            combined.append({
                'id': pc['id'],
                'component_name': pc['component_name'],
                'component_type': pc['component_type'],
                'payslip_amount': pc['amount'],
                'structure_amount': None,
                'is_active': None,
                'date_created': None,
                'date_updated': None,
                'employee': str(obj.employee),
                'component': pc['component_name']
            })
            component_names.add(pc['component_name'])

        # Process EmployeeSalaryStructure entries
        for ss in salary_structures:
            if ss['component'] in component_names:
                # Update existing component with structure data
                for item in combined:
                    if item['component_name'] == ss['component']:
                        item['structure_amount'] = ss['amount']
                        item['is_active'] = ss['is_active']
                        item['date_created'] = ss['date_created']
                        item['date_updated'] = ss['date_updated']
                        break
            else:
                # Add new component from salary structure
                combined.append({
                    'id': ss['id'],
                    'component_name': ss['component'],
                    'component_type': ss['component_type'],
                    'payslip_amount': None,
                    'structure_amount': ss['amount'],
                    'is_active': ss['is_active'],
                    'date_created': ss['date_created'],
                    'date_updated': ss['date_updated'],
                    'employee': ss['employee'],
                    'component': ss['component']
                })

        return combined
class LoanTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanType
        fields = '__all__'
    def validate(self, attrs):
        loan_type=attrs.get("loan_type")
        loan=LoanType.objects.filter(loan_type=loan_type)
        if self.instance:
            loan=loan.exclude(id=self.instance.id)
        if loan.exists():
            raise serializers.ValidationError({ f"{loan_type} is already exists."
        })
        return attrs
    def to_representation(self, instance):
        rep = super(LoanTypeSerializer, self).to_representation(instance)
        rep['branch'] = [
            branch.branch_name
            for branch in instance.branch.all()
        ]
        return rep

class LoanApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanApplication
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(LoanApplicationSerializer, self).to_representation(instance)
        if instance.employee:
            rep['employee'] =instance.employee.emp_code
        if instance.loan_type:
            rep['loan_type'] =instance.loan_type.loan_type
        if instance.branch:
            rep['branch'] =instance.branch.branch_name
        return rep
    def validate(self, data):
        loan_type = data.get('loan_type')
        employee = data.get('employee')

        # ---------------- BASIC CHECKS ----------------
        if not loan_type:
            raise serializers.ValidationError({
                "loan_type": "Loan type is required."
            })

        if not employee:
            raise serializers.ValidationError({
                "employee": "Employee is required."
            })

        # ---------------- SAFE BRANCH ACCESS ----------------
        branch = getattr(employee, "emp_branch_id", None)

        if not branch:
            raise serializers.ValidationError({
                "employee": "Employee branch is not assigned."
            })

        # ---------------- GET WORKFLOW ----------------
        workflow = LoanApprovalWorkflow.objects.filter(
            loan_type=loan_type,
            branch=branch
        ).first()

        # ✅ FIX 1: fallback if branch-based not found
        if not workflow:
            workflow = LoanApprovalWorkflow.objects.filter(
                loan_type=loan_type
            ).first()

        if not workflow:
            raise serializers.ValidationError({
                "loan_type": "Approval workflow is not configured for this loan type."
            })

        approval_type = workflow.approval_type

        # ---------------- GET FIRST LEVEL ----------------
        first_level = workflow.loan_levels.order_by('level').first()

        # ✅ FIX 2: only enforce level for multi approval
        if approval_type == 'multi_approval' and not first_level:
            raise serializers.ValidationError({
                "loan_type": "Approval levels are not configured."
            })

        # ---------------- REPORTING MANAGER ----------------
        if approval_type == 'reporting_manager':
            if not getattr(employee, "emp_reporting_manager", None):
                raise serializers.ValidationError({
                    "employee": "This employee does not have a reporting manager assigned."
                })

        # ---------------- MULTI APPROVAL ----------------
        if approval_type == 'multi_approval':
            if not first_level.approver:
                raise serializers.ValidationError({
                    "loan_type": f"Approver is not configured for level {first_level.level}."
                })

        return data
class LoanRepaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanRepayment
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(LoanRepaymentSerializer, self).to_representation(instance)
        if instance.loan:
            rep['loan'] =instance.loan.loan_type.loan_type
        return rep
class LoanApprovalLevelsSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanApprovalLevels
        fields = [
            "id",
            "level",
            "role",
            "approver",
            "escalate_to",
            "escalate_after_days",
            "escalate_after_hours",
            "escalate_after_minutes",
        ]
        read_only_fields = ('workflow',)
    def to_representation(self, instance):
        rep = super(LoanApprovalLevelsSerializer, self).to_representation(instance)
        if instance.approver:
            rep['approver'] =instance.approver.username
        return rep

class LoanApprovalWorkflowSerializer(serializers.ModelSerializer):
    levels = LoanApprovalLevelsSerializer(source='loan_levels', many=True)

    class Meta:
        model = LoanApprovalWorkflow
        fields = '__all__'

    def create(self, validated_data):
        # ✅ FIX: use source name
        levels_data = validated_data.pop('loan_levels', [])
        branches = validated_data.pop('branch', [])

        workflow = LoanApprovalWorkflow.objects.create(**validated_data)

        if branches:
            workflow.branch.set(branches)

        # ---------------- MULTI APPROVAL ----------------
        if workflow.approval_type == 'multi_approval':

            for level_data in levels_data:
                level_data.pop('workflow', None)

                LoanApprovalLevels.objects.create(
                    workflow=workflow,
                    **level_data
                )

        # ---------------- NO APPROVAL ----------------
        elif workflow.approval_type == 'no_approval':

            LoanApprovalLevels.objects.create(
                workflow=workflow,
                level=1,
                role='Auto Level',
                approver=None
            )

        # ---------------- REPORTING MANAGER ----------------
        elif workflow.approval_type == 'reporting_manager':

            reporting_manager = None

            request = self.context.get('request')

            if request and hasattr(request.user, 'employee'):
                reporting_manager = request.user.employee.reporting_manager

            LoanApprovalLevels.objects.create(
                workflow=workflow,
                # level=1,
                role='Reporting Manager',
                approver=reporting_manager
            )

        return workflow

    def update(self, instance, validated_data):
        # ✅ FIX: use source name
        levels_data = validated_data.pop('loan_levels', None)
        branches = validated_data.pop('branch', None)

        instance.approval_type = validated_data.get(
            'approval_type',
            instance.approval_type
        )

        instance.loan_type = validated_data.get(
            'loan_type',
            instance.loan_type
        )

        instance.save()

        if branches is not None:
            instance.branch.set(branches)

        # ---------------- RESET LEVELS ----------------
        instance.loan_levels.all().delete()

        # ---------------- MULTI APPROVAL ----------------
        if instance.approval_type == 'multi_approval':

            if levels_data is not None:

                for level_data in levels_data:
                    level_data.pop('workflow', None)

                    LoanApprovalLevels.objects.create(
                        workflow=instance,
                        **level_data
                    )

        # ---------------- NO APPROVAL ----------------
        elif instance.approval_type == 'no_approval':

            LoanApprovalLevels.objects.create(
                workflow=instance,
                level=1,
                role='Auto Level',
                approver=None
            )

        # ---------------- REPORTING MANAGER ----------------
        elif instance.approval_type == 'reporting_manager':

            reporting_manager = None

            request = self.context.get('request')

            if request and hasattr(request.user, 'employee'):
                reporting_manager = request.user.employee.reporting_manager

            LoanApprovalLevels.objects.create(
                workflow=instance,
                level=1,
                role='Reporting Manager',
                approver=reporting_manager
            )

        return instance
    
    def to_representation(self, instance):
        rep = super( LoanApprovalWorkflowSerializer, self).to_representation(instance)
        # if instance.branch.exists():
        rep['branch'] = [b.branch_name for b in instance.branch.all()]
        if instance.loan_type:
             rep['loan_type'] =instance.loan_type.loan_type
        return rep
    
class LoanApprovalSerializer(serializers.ModelSerializer):
    delegation_details = serializers.SerializerMethodField()

    class Meta:
        model = LoanApproval
        fields = '__all__'

    def get_delegation_details(self, obj):
        return {
            "delegate_to_id": obj.deligate_to.id if obj.deligate_to else None,
            "delegate_to": obj.deligate_to.username if obj.deligate_to else None,
            "response": obj.deligate_response,
            "is_deligate": obj.is_deligate,
        }
    

    def to_representation(self, instance):
        rep = super(LoanApprovalSerializer, self).to_representation(instance)
        if instance.loan_request:
            rep['loan_request'] =instance.loan_request.loan_type.loan_type 
        if instance.employee_id:
            try:
                emp = emp_master.objects.get(id=instance.employee_id)
                rep['employee_id'] = emp.emp_code
            except emp_master.DoesNotExist:
                rep['employee_id'] = None
        if instance.loan_request:
            rep['document_number']= getattr(instance.loan_request,'document_number')
        if instance.deligate_to:
            rep['deligate_to'] = instance.deligate_to.id if instance.deligate_to else None
        return rep
    
    
class SIFSerializer(serializers.Serializer):
    payroll_run_id = serializers.IntegerField()
    department_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=True
    )
    employee_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=True
    )
    branch_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=True
    )
    def validate_payroll_run_id(self, value):
        if not PayrollRun.objects.filter(id=value).exists():
            raise serializers.ValidationError("Invalid PayrollRun ID")
        return value
    def generate_sif_data(self):
        payroll_run = PayrollRun.objects.get(id=self.validated_data['payroll_run_id'])
        employees = payroll_run.get_employees().filter(is_active=True)

        # ✅ Apply branch filter if provided
        branch_ids = self.validated_data.get("branch_ids", [])
        if branch_ids:
            employees = employees.filter(emp_branch_id__in=branch_ids)

        # ✅ Apply department filter if provided
        department_ids = self.validated_data.get("department_ids", [])
        if department_ids:
            employees = employees.filter(emp_dept_id__in=department_ids)

        # ✅ Apply employee filter if provided
        employee_ids = self.validated_data.get("employee_ids", [])
        if employee_ids:
            employees = employees.filter(id__in=employee_ids)

        month, year = payroll_run.month, payroll_run.year
        last_day = calendar.monthrange(year, month)[1]
        pay_start_date = f"{year}-{month:02d}-01"
        pay_end_date = f"{year}-{month:02d}-{last_day}"

        sif_data = []
        total_salary = Decimal("0.0")
        skipped_employees = []

        for employee in employees:
            # ✅ Get the employee’s active bank detail
            bank_detail = employee.bank_details.filter(is_active=True).first()
            if not bank_detail:
                skipped_employees.append({
                    "emp_code": employee.emp_code,
                    "reason": "Missing or inactive bank details"
                })
                continue

            # ✅ Person ID validation
            if not employee.person_id or len(employee.person_id) != 14:
                skipped_employees.append({
                    "emp_code": employee.emp_code,
                    "reason": "Invalid or missing Person ID (14 digits required)"
                })
                continue

            # ✅ Routing code validation
            if not bank_detail.route_code or len(bank_detail.route_code) != 9:
                skipped_employees.append({
                    "emp_code": employee.emp_code,
                    "reason": "Invalid or missing Routing Code (9 digits required)"
                })
                continue

            # ✅ IBAN validation
            if not bank_detail.iban_number or len(bank_detail.iban_number) != 23:
                skipped_employees.append({
                    "emp_code": employee.emp_code,
                    "reason": "Invalid or missing IBAN (23 characters required)"
                })
                continue

            # ✅ Calculate fixed & variable income
            fixed_income = sum(
                struct.amount or Decimal("0.0")
                for struct in employee.salary_structures.filter(
                    component__component_value_type="fixed", is_active=True
                )
            )
            variable_income = sum(
                struct.amount or Decimal("0.0")
                for struct in employee.salary_structures.filter(
                    component__is_fixed=False, is_active=True
                )
            )

            total_salary += fixed_income + variable_income

            row = {
                "Type": "EDR",
                "Person ID": employee.person_id,
                "Routing Code": bank_detail.route_code,
                "IBAN Number": bank_detail.iban_number,
                "Pay Start Date": pay_start_date,
                "Pay End Date": pay_end_date,
                "Number of Days": last_day,
                "Fixed Income": f"{fixed_income:.2f}",
                "Variable Income": f"{variable_income:.2f}",
            }
            sif_data.append(row)
        return sif_data, total_salary, skipped_employees
    
class AdvanceSalaryRequestSerializer(serializers.ModelSerializer):
    currency_details = serializers.SerializerMethodField()
    class Meta:
        model = AdvanceSalaryRequest
        fields = '__all__'
    def get_currency_details(self, obj):
        request = self.context.get("request")
        if request and hasattr(request, "tenant") and request.tenant.currency:
            currency = request.tenant.currency
            return {
                "currency_name": currency.currency_name,
                "currency_code": currency.currency_code,
                "symbol": currency.symbol
            }
        return None
    
    def validate(self, data):
        employee = data.get('employee')

        if not employee:
            raise serializers.ValidationError({
                "employee": "Employee is required."
            })

        # ✅ correct branch filtering
        workflow = AdvanceApprovalWorkflow.objects.filter(
            branch=employee.emp_branch_id
        ).first()

        if not workflow:
            raise serializers.ValidationError({
                "workflow": "Approval workflow is not configured for this branch."
            })

        approval_type = workflow.approval_type

        # ---------------- NO APPROVAL ----------------
        if approval_type == 'no_approval':
            return data

        # ---------------- REPORTING MANAGER ----------------
        if approval_type == 'reporting_manager':
            if not employee.emp_reporting_manager:
                raise serializers.ValidationError({
                    "employee": "This employee does not have a reporting manager assigned."
                })
            return data

        # ---------------- MULTI APPROVAL ----------------
        if approval_type == 'multi_approval':

            first_level = workflow.advance_levels.order_by('level').first()

            if not first_level:
                raise serializers.ValidationError({
                    "approval_level": "Approval levels are not configured."
                })

            if not first_level.approver:
                raise serializers.ValidationError({
                    "approver": f"No approver configured for level {first_level.level}."
                })

        return data
    def to_representation(self, instance):
        rep = super().to_representation(instance)
         # ✅ employee code
        if instance.employee:
            rep['employee'] = instance.employee.emp_code
        if instance.branch:
            rep['branch']=instance.branch.branch_name
        return rep
    
class AdvanceSalaryApprovalSerializer(serializers.ModelSerializer):
    delegation_details = serializers.SerializerMethodField()
    class Meta:
        model = AdvanceSalaryApproval
        fields = '__all__'

    def get_delegation_details(self, obj):
        return {
            "delegate_to_id": obj.deligate_to.id if obj.deligate_to else None,
            "delegate_to": obj.deligate_to.username if obj.deligate_to else None,
            "response": obj.deligate_response,
            "is_deligate": obj.is_deligate,
        }
    
    def to_representation(self, instance):
        rep = super().to_representation(instance)

        if instance.request:
            rep['request'] = instance.request.document_number

        if instance.employee:
            rep['employee'] = instance.employee.emp_code

        if instance.approver:
            rep['approver'] = instance.approver.username

        if instance.deligate_to:
            rep['deligate_to'] = instance.deligate_to.id if instance.deligate_to else None

        return rep

class AdvanceCommonWorkflowSerializer(serializers.ModelSerializer):
    approver = serializers.PrimaryKeyRelatedField(queryset=CustomUser.objects.all(),required=False,allow_null=True)
    
    class Meta:
        model = AdvanceCommonWorkflow
        fields = '__all__'
    def to_internal_value(self, data):
        data = data.copy()

        if data.get("approver") in [0, "0"]:
            data["approver"] = None

        return super().to_internal_value(data)

    def to_representation(self, instance):
        rep = super().to_representation(instance)

        # ✅ Fix role
        if not instance.role:
            if instance.workflow.approval_type == 'reporting_manager':
                rep['role'] = "Reporting Manager"
            elif instance.workflow.approval_type == 'no_approval':
                rep['role'] = "Auto Approval"
            else:
                rep['role'] = "Approver"

        # ✅ No Approval
        if instance.workflow.approval_type == 'no_approval':
            rep['approver'] = None

        # ✅ Reporting Manager
        elif instance.workflow.approval_type == 'reporting_manager':

            employee = self.context.get('employee')

            if employee and getattr(employee, 'emp_reporting_manager', None):
                rep['approver'] = employee.emp_reporting_manager.username
            else:
                rep['approver'] = None

        # ✅ Multi Approval
        elif instance.approver:
            rep['approver'] = instance.approver.username

        return rep
class AdvanceApprovalWorkflowSerializer(serializers.ModelSerializer):
    levels = AdvanceCommonWorkflowSerializer(many=True,source='advance_levels',required=False)

    class Meta:
        model = AdvanceApprovalWorkflow
        fields = '__all__'

    def to_representation(self, instance):
        rep = super().to_representation(instance)

        if instance.branch.exists():
            rep['branch'] = [b.branch_name for b in instance.branch.all()]

        return rep

    def create(self, validated_data):
        levels_data = validated_data.pop('advance_levels', [])
        branches = validated_data.pop('branch', [])

        workflow = AdvanceApprovalWorkflow.objects.create(**validated_data)

        if branches:
            workflow.branch.set(branches)

        # ---------------- MULTI APPROVAL ----------------
        if workflow.approval_type == 'multi_approval':

            for level_data in levels_data:
                level_data.pop('workflow', None)

                AdvanceCommonWorkflow.objects.create(
                    workflow=workflow,
                    **level_data
                )

        # ---------------- NO APPROVAL ----------------
        elif workflow.approval_type == 'no_approval':

            AdvanceCommonWorkflow.objects.create(
                workflow=workflow,
                level=1,
                role='Auto Level',
                approver=None
            )

        # ---------------- REPORTING MANAGER ----------------
        elif workflow.approval_type == 'reporting_manager':

            reporting_manager = None

            request = self.context.get('request')

            if request and hasattr(request.user, 'employee'):
                reporting_manager = request.user.employee.reporting_manager

            AdvanceCommonWorkflow.objects.create(
                workflow=workflow,
                level=1,
                role='Reporting Manager',
                approver=reporting_manager
            )

        return workflow


    def update(self, instance, validated_data):
        levels_data = validated_data.pop('advance_levels', None)
        branches = validated_data.pop('branch', None)

        # ---------------- BASIC UPDATE ----------------
        instance.approval_type = validated_data.get(
            'approval_type',
            instance.approval_type
        )
        instance.save()

        # ---------------- BRANCH UPDATE ----------------
        if branches is not None:
            instance.branch.set(branches)

        # ---------------- LEVELS UPDATE (SAFE DIFF LOGIC) ----------------
        if levels_data is not None:

            # ✅ FIX 1: enforce single level rule for reporting_manager / no_approval
            if instance.approval_type in ['reporting_manager', 'no_approval']:
                levels_data = levels_data[:1]

            existing_levels = {
                lvl.id: lvl for lvl in instance.advance_levels.all()
            }

            incoming_ids = []

            for level_data in levels_data:
                level_id = level_data.get('id')

                # ================= UPDATE EXISTING LEVEL =================
                if level_id and level_id in existing_levels:

                    obj = existing_levels[level_id]

                    obj.level = level_data.get('level', obj.level)

                    # ✅ FIX 2 (keep consistent rule enforcement)
                    if instance.approval_type == 'no_approval':
                        obj.approver = None
                        obj.role = 'Auto Level'

                    elif instance.approval_type == 'reporting_manager':

                        reporting_manager = None

                        request = self.context.get('request')

                        if request and hasattr(request.user, 'employee'):
                            reporting_manager = request.user.employee.reporting_manager

                        obj.approver = reporting_manager
                        obj.role = 'Reporting Manager'

                    else:
                        obj.approver = level_data.get('approver', obj.approver)
                        obj.role = level_data.get('role', obj.role)

                    obj.escalate_to = level_data.get('escalate_to', obj.escalate_to)
                    obj.escalate_after_days = level_data.get('escalate_after_days', obj.escalate_after_days)
                    obj.escalate_after_hours = level_data.get('escalate_after_hours', obj.escalate_after_hours)
                    obj.escalate_after_minutes = level_data.get('escalate_after_minutes', obj.escalate_after_minutes)

                    obj.save()
                    incoming_ids.append(obj.id)

                # ================= CREATE NEW LEVEL =================
                else:
                    level_data.pop('workflow', None)

                    # ✅ FIX 3: enforce rule here too
                    if instance.approval_type == 'no_approval':

                        level_data['approver'] = None
                        level_data['role'] = 'Auto Level'

                    elif instance.approval_type == 'reporting_manager':

                        reporting_manager = None

                        request = self.context.get('request')

                        if request and hasattr(request.user, 'employee'):
                            reporting_manager = request.user.employee.reporting_manager

                        level_data['approver'] = reporting_manager
                        level_data['role'] = 'Reporting Manager'

                    new_obj = AdvanceCommonWorkflow.objects.create(
                        workflow=instance,
                        **level_data
                    )

                    incoming_ids.append(new_obj.id)

            # ================= DELETE REMOVED LEVELS =================
            instance.advance_levels.exclude(id__in=incoming_ids).delete()

        return instance

class PayslipApprovalSerializer(serializers.ModelSerializer):
    request = PayslipSerializer(read_only=True)
    class Meta:
        model = PayslipApproval
        fields = '__all__'
class PayslipCommonWorkflowSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayslipCommonWorkflow
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(PayslipCommonWorkflowSerializer, self).to_representation(instance)
        if instance.approver:  
            rep['approver'] = instance.approver.username 
        return rep
class PayslipApprovalWorkflowSerializer(serializers.ModelSerializer):
    levels = PayslipCommonWorkflowSerializer(many=True,source='payslip_levels')
    # created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = PayslipApprovalWorkflow
        fields = '__all__'

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        if instance.branch.exists():
            rep['branch'] = [b.branch_name for b in instance.branch.all()]

        return rep

    def create(self, validated_data):
        levels_data = validated_data.pop('payslip_levels', [])

        branches = validated_data.pop('branch', [])
        workflow = PayslipApprovalWorkflow.objects.create(**validated_data)

        if branches:
            workflow.branch.set(branches)

        for level_data in levels_data:
            level_data.pop('workflow', None)

            PayslipCommonWorkflow.objects.create(
                workflow=workflow,
                **level_data
            )

        return workflow

    def update(self, instance, validated_data):
        levels_data = validated_data.pop('payslip_levels', None)
        branches = validated_data.pop('branch', None)

        instance.approval_type = validated_data.get(
            'approval_type',
            instance.approval_type
        )
        instance.save()

        if branches is not None:
            instance.branch.set(branches)

        if levels_data is not None:
            instance.payslip_levels.all().delete()

            for level_data in levels_data:
                level_data.pop('workflow', None)

                PayslipCommonWorkflow.objects.create(
                    workflow=instance,
                    **level_data
                )

        return instance
class AirTicketRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AirTicketRule
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(AirTicketRuleSerializer, self).to_representation(instance)
        if instance.policy:  
            rep['policy'] = instance.policy.name 
        return rep

class AirTicketPolicySerializer(serializers.ModelSerializer):
    rules = AirTicketRuleSerializer(many=True, read_only=True)
    class Meta:
        model = AirTicketPolicy
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(AirTicketPolicySerializer, self).to_representation(instance)
        if instance.country:  
            rep['country'] = instance.country.country_name
        if instance.eligible_departments.exists():  
            rep['eligible_departments'] = [dept.dept_name for dept in instance.eligible_departments.all()]

        if instance.eligible_designations.exists():  
            rep['eligible_designations'] = [desg.desgntn_job_title for desg in instance.eligible_designations.all()]

        if instance.eligible_categories.exists():  
            rep['eligible_categories'] = [cat.ctgry_title for cat in instance.eligible_categories.all()]

        return rep
class AirTicketAllocationSerializer(serializers.ModelSerializer):
    # employee = serializers.PrimaryKeyRelatedField(queryset=emp_master.objects.all())
    # policy = serializers.PrimaryKeyRelatedField(queryset=AirTicketPolicy.objects.all())
    # allocated_by = serializers.PrimaryKeyRelatedField(queryset=emp_master.objects.all(), allow_null=True)

    class Meta:
        model = AirTicketAllocation
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(AirTicketAllocationSerializer, self).to_representation(instance)
        if instance.policy:  
            rep['policy'] = instance.policy.name 
        if instance.employee:  
            rep['employee'] = instance.employee.emp_code
        return rep
class AirTicketRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = AirTicketRequest
        fields = '__all__'
    def to_representation(self, instance):
        rep = super().to_representation(instance)

        if instance.employee:
            rep['employee'] = instance.employee.emp_code

        if instance.allocation:
            rep['allocation'] = instance.allocation.policy.name
        
        if instance.branch:
            rep['branch'] = instance.branch.branch_name

        return rep

    def validate(self, data):
        employee = data.get('employee')
        branch = data.get('branch')

        if not branch:
            return data

        # ✅ FIX: correct ManyToMany filtering
        workflow = AirticketApprovalWorkflow.objects.filter(
            branch=branch
        ).first()

        if not workflow:
            raise serializers.ValidationError({
                "branch": "No workflow configured for this branch."
            })

        # ✅ Reporting manager check
        if workflow.approval_type == 'reporting_manager':
            if not getattr(employee, 'emp_reporting_manager', None):
                raise serializers.ValidationError({
                    "employee": "This employee does not have a reporting manager assigned."
                })

        return data
    
class AirtcketApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = AirticketApproval
        fields = '__all__'
    def to_representation(self, instance):
        rep = super().to_representation(instance)

        if instance.request:
            rep['request'] = instance.request.document_number

        if instance.employee:
            rep['employee'] = instance.employee.emp_code

        if instance.approver:
            rep['approver'] = instance.approver.username

        return rep
    
class AirticketWorkflowSerializer(serializers.ModelSerializer):
    workflow = serializers.PrimaryKeyRelatedField(read_only=True)
    class Meta:
        model = AirticketWorkflow
        fields = '__all__'
    def to_representation(self, instance):
        rep = super().to_representation(instance)

        # ✅ Role fallback (same as resignation)
        if not instance.role:
            if instance.workflow.approval_type == 'reporting_manager':
                rep['role'] = "Reporting Manager"
            elif instance.workflow.approval_type == 'no_approval':
                rep['role'] = "Auto Approval"
            else:
                rep['role'] = "Approver"

        # ✅ Reporting manager logic
        if instance.workflow.approval_type == 'reporting_manager':
            employee = self.context.get('employee')

            if employee and getattr(employee, 'emp_reporting_manager', None):
                rep['approver'] = employee.emp_reporting_manager.username
            else:
                rep['approver'] = None

        elif instance.approver:
            rep['approver'] = instance.approver.username
        else:
            rep['approver'] = None

        return rep

    def to_internal_value(self, data):
        # ✅ FIX: handle "Invalid pk 0"
        if data.get('approver') == 0:
            data['approver'] = None
        return super().to_internal_value(data)
    
class AirticketEmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = AirticketEmailTemplate
        fields = '__all__'
class AirticketApprovalWorkflowSerializer(serializers.ModelSerializer):
    levels = AirticketWorkflowSerializer(many=True, source='airticket_levels')

    class Meta:
        model = AirticketApprovalWorkflow
        fields = '__all__'
        
    def to_representation(self, instance):
        rep = super(AirticketApprovalWorkflowSerializer, self).to_representation(instance)
        if instance.branch:
           rep['branch'] = [branch.branch_name for branch in instance.branch.all()]
           return rep

    def create(self, validated_data):
        levels_data = validated_data.pop('airticket_levels', [])   # ✅ FIX
        branches = validated_data.pop('branch', [])

        workflow = AirticketApprovalWorkflow.objects.create(**validated_data)
        workflow.branch.set(branches)

        for level_data in levels_data:
            AirticketWorkflow.objects.create(
                workflow=workflow,   # 🔥 auto link
                **level_data
            )

        return workflow

    def update(self, instance, validated_data):
        levels_data = validated_data.pop('airticket_levels', None)
        branches = validated_data.pop('branch', None)

        instance.approval_type = validated_data.get(
            'approval_type',
            instance.approval_type
        )
        instance.save()

        if branches is not None:
            instance.branch.set(branches)

        if levels_data is not None:

            # ✅ FIX 1: enforce single level rule if needed
            if instance.approval_type in ['reporting_manager', 'no_approval']:
                levels_data = levels_data[:1]

            instance.airticket_levels.all().delete()

            for level_data in levels_data:

                # ✅ FIX 2: remove unsafe FK injection
                level_data.pop('workflow', None)

                # ✅ FIX 3: enforce business rule
                if instance.approval_type in ['reporting_manager', 'no_approval']:
                    level_data['approver'] = None

                AirticketWorkflow.objects.create(
                    workflow=instance,
                    **level_data
                )

        return instance

class AirticketEscalationRuleSerializer(serializers.ModelSerializer):
    approver_name = serializers.CharField(source='approver.username', read_only=True)
    escalate_to_name = serializers.CharField(source='escalate_to.username', read_only=True)

    class Meta:
        model = AirticketWorkflow
        fields = [
            'id',
            'level',
            'role',
            'approver',
            'approver_name',
            'escalate_to',
            'escalate_to_name',
            'escalate_after_days',
            'escalate_after_hours',
            'escalate_after_minutes',
        ]
        read_only_fields = [
            'level', 'role', 'approver',  'approver_name', 'escalate_to_name'
        ]
class LoanEmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanEmailTemplate
        fields = '__all__'
    def validate(self, attrs):
        template_type=attrs.get("template_type")
        temp=LoanEmailTemplate.objects.filter(template_type=template_type)
        if self.instance:
            temp=temp.exclude(id=self.instance.id)
        if temp.exists():
            raise serializers.ValidationError({"template_name": f"{template_type} template already exists."
        })
        return attrs
class LoanNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanNotification
        fields = '__all__'
class AdvSalaryEmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdvanceSalaryEmailTemplate
        fields = '__all__'
    def validate(self, attrs):
        template_type=attrs.get("template_type")
        temp=AdvanceSalaryEmailTemplate.objects.filter(template_type=template_type)
        if self.instance:
            temp=temp.exclude(id=self.instance.id)
        if temp.exists():
            raise serializers.ValidationError({"template_name": f"{template_type} template already exists."
        })
        return attrs
class AdvSalaryNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdvanceSalaryNotification
        fields = '__all__'

class AdvSalaryEscalationRuleSerializer(serializers.ModelSerializer):
    approver_name = serializers.CharField(source='approver.username', read_only=True)
    escalate_to_name = serializers.CharField(source='escalate_to.username', read_only=True)

    class Meta:
        model = AdvanceCommonWorkflow
        fields = [
            'id',
            'level',
            'role',
            'approver',
            'approver_name',
            'escalate_to',
            'escalate_to_name',
            'escalate_after_days',
            'escalate_after_hours',
            'escalate_after_minutes',
        ]
        read_only_fields = [
            'level',
            'role',
            'approver',
            'approver_name',
            'escalate_to_name'
        ]
        
class LoanEscalationRuleSerializer(serializers.ModelSerializer):
    loan_type = serializers.PrimaryKeyRelatedField(source='workflow.loan_type',read_only=True)
    loan_type_name = serializers.CharField(source='loan_type.name', read_only=True)
    approver_name = serializers.CharField(source='approver.username', read_only=True)
    escalate_to_name = serializers.CharField(source='escalate_to.username', read_only=True)

    class Meta:
        model = LoanApprovalLevels
        fields = [
            'id',
            'level',
            'role',
            'loan_type',
            'loan_type_name',
            'approver',
            'approver_name',
            'escalate_to',
            'escalate_to_name',
            'escalate_after_days',
            'escalate_after_hours',
            'escalate_after_minutes',
        ]
        read_only_fields = [
            'level', 'role', 'approver', 'loan_type', 
            'loan_type_name', 'approver_name', 'escalate_to_name'
        ]
class PayStructureSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayStructure
        fields = '__all__'

class PayslipLeaveSerializer(serializers.ModelSerializer):
    leave_type_name = serializers.CharField(
        source="leave_type.name", read_only=True
    )

    class Meta:
        model = PayslipLeave
        fields = ["leave_type", "leave_type_name", "days"]

