from rest_framework import serializers
from .models import (SalaryComponent,EmployeeSalaryStructure,PayrollRun,Payslip,PayslipComponent,LoanType,LoanApplication,
                    LoanRepayment,LoanApprovalLevels,LoanApproval,AdvanceSalaryRequest,AdvanceSalaryApproval,AdvanceCommonWorkflow,PayslipApproval,PayslipCommonWorkflow,AirTicketPolicy,AirTicketAllocation,AirTicketRequest,
                    LoanEmailTemplate,LoanNotification,AdvanceSalaryEmailTemplate,AdvanceSalaryNotification)

import calendar
from EmpManagement .models import EmployeeBankDetail,emp_master


class SalaryComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalaryComponent
        fields = '__all__'
class EmployeeSalaryStructureSerializer(serializers.ModelSerializer):
    # For readable output
    emp_code = serializers.SerializerMethodField(read_only=True)
    component_name = serializers.CharField(source='component.name', read_only=True)
    component_type = serializers.CharField(source='component.get_component_type_display', read_only=True)

    class Meta:
        model = EmployeeSalaryStructure
        # Include 'employee' and 'component' for input
        fields = [
            'id', 'employee', 'emp_code',
            'component', 'component_name', 'component_type',
            'amount', 'is_active', 'date_created', 'date_updated'
        ]
        extra_kwargs = {
            'employee': {'write_only': False},  # Allow it in input and output
            'component': {'write_only': True}   # Hide component ID in output
        }

    def get_emp_code(self, obj):
        return obj.employee.emp_code

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

class PaySlipComponentSerializer(serializers.ModelSerializer):
    component_name = serializers.CharField(source='component.name', read_only=True)
    component_type = serializers.CharField(source='component.get_component_type_display', read_only=True)

    class Meta:
        model = PayslipComponent
        fields = ['id', 'component_name', 'component_type', 'amount']

class PayslipSerializer(serializers.ModelSerializer):
    currency_details = serializers.SerializerMethodField()
    payroll_run = PayrollRunSerializer(read_only=True)
    employee = serializers.StringRelatedField()
    components = serializers.SerializerMethodField()
    currency_details = serializers.SerializerMethodField()

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
class LoanApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanApplication
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(LoanApplicationSerializer, self).to_representation(instance)
        if instance.employee:
            rep['employee'] =instance.employee.emp_first_name
        if instance.loan_type:
            rep['loan_type'] =instance.loan_type.loan_type
        return rep
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
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(LoanApprovalLevelsSerializer, self).to_representation(instance)
        if instance.approver:
            rep['approver'] =instance.approver.username
        return rep
class LoanApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanApproval
        fields = '__all__'

class SIFSerializer(serializers.Serializer):
    payroll_run_id = serializers.IntegerField()

    def validate_payroll_run_id(self, value):
        if not PayrollRun.objects.filter(id=value).exists():
            raise serializers.ValidationError("Invalid PayrollRun ID")
        return value

    def generate_sif_data(self):
        payroll_run = PayrollRun.objects.get(id=self.validated_data['payroll_run_id'])
        employees = payroll_run.get_employees()
        month, year = payroll_run.month, payroll_run.year
        last_day = calendar.monthrange(year, month)[1]
        pay_start_date = f"{year}-{month:02d}-01"
        pay_end_date = f"{year}-{month:02d}-{last_day}"
    
        sif_data = []
        total_salary = 0  # Initialize total
    
        for employee in employees:
            if not employee.person_id:
                raise serializers.ValidationError(f"Employee {employee.emp_code} is missing a valid 14-digit Person ID")
    
            try:
                bank_detail = employee.bank_details
            except EmployeeBankDetail.DoesNotExist:
                raise serializers.ValidationError(f"Employee {employee.emp_code} has no bank details")
    
            if not bank_detail.route_code:
                raise serializers.ValidationError(f"Employee {employee.emp_code} is missing a valid 9-digit routing code")
            if not bank_detail.iban_number:
                raise serializers.ValidationError(f"Employee {employee.emp_code} is missing a valid 23-character IBAN")
    
            fixed_income = sum(
                structure.amount or 0 for structure in employee.salary_structures.filter(
                    component__is_fixed=True, is_active=True
                )
            )
            variable_income = sum(
                salary_struct.amount or 0 for salary_struct in EmployeeSalaryStructure.objects.filter(
                    employee=employee, is_active=True
                )
            )
    
            # Add to total salary
            total_salary += fixed_income + variable_income
    
            row = {
                'Type': 'EDR',
                'Person ID': employee.person_id,
                'Routing Code': bank_detail.route_code,
                'IBAN Number': bank_detail.iban_number,
                'Pay Start Date': pay_start_date,
                'Pay End Date': pay_end_date,
                'Number of Days': last_day,
                'Fixed Income': f"{fixed_income:.2f}",
                'Variable Income': f"{variable_income:.2f}",
            }
            sif_data.append(row)
    
        return sif_data, total_salary  # ✅ Now returning two values
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
class AdvanceSalaryApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdvanceSalaryApproval
        fields = '__all__'
class AdvanceCommonWorkflowSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdvanceCommonWorkflow
        fields = '__all__'

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
class AirTicketPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = AirTicketPolicy
        fields = '__all__'

class AirTicketAllocationSerializer(serializers.ModelSerializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=emp_master.objects.all())
    policy = serializers.PrimaryKeyRelatedField(queryset=AirTicketPolicy.objects.all())
    # allocated_by = serializers.PrimaryKeyRelatedField(queryset=emp_master.objects.all(), allow_null=True)

    class Meta:
        model = AirTicketAllocation
        fields = '__all__'
class AirTicketRequestSerializer(serializers.ModelSerializer):
    # allocation = AirTicketAllocationSerializer(read_only=True)
    # allocation_id = serializers.PrimaryKeyRelatedField(
    #     queryset=AirTicketAllocation.objects.all(), source='allocation', write_only=True
    # )
    # employee = EmpSerializer(read_only=True)

    class Meta:
        model = AirTicketRequest
        fields = '__all__'

class LoanEmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanEmailTemplate
        fields = '__all__'
class LoanNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanNotification
        fields = '__all__'
class AdvSalaryEmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdvanceSalaryEmailTemplate
        fields = '__all__'
class AdvSalaryNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdvanceSalaryNotification
        fields = '__all__'