
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.contrib.contenttypes.models import ContentType
import datetime
from OrganisationManager .models import AssetAllocation
from calendars.serializer import WeekendCalendarSerailizer,HolidayCalandarSerializer,HolidaySerializer,EmployeeLeaveBalanceSerializer
from calendars .models import holiday
from PayrollManagement .serializer import AdvanceSalaryRequestSerializer,LoanApplicationSerializer,PayslipSerializer
from decimal import Decimal
from calendar import month_name
# from UserManagement.serializers import CustomUserSerializer


from .models import (emp_family,EmpJobHistory,EmpQualification,Emp_Documents,EmpLeaveRequest,emp_master,Emp_CustomField,
                    EmpFamily_CustomField,EmpJobHistory_CustomField,EmpQualification_CustomField,EmpDocuments_CustomField,
                    notification,Report,Doc_Report,RequestType,
                    GeneralRequest,GeneralRequestReport,EmployeeMarketingSkill,EmployeeProgramSkill,EmployeeLangSkill,Approval,
                    ApprovalLevel,RequestNotification,Emp_CustomFieldValue,EmailTemplate,EmailConfiguration,SelectedEmpNotify,NotificationSettings,
                    DocExpEmailTemplate,CommonWorkflow,Doc_CustomFieldValue,EmployeeBankDetail,Fam_CustomFieldValue,Qualification_CustomFieldValue,
                    JobHistory_CustomFieldValue,DocumentRequest,DocumentApprovalLevel,DocumentApproval,ResignationApprovalLevel,ResignationApproval,DocRequestEmailTemplate,
                    DocRequestNotification,EndOfService,EmployeeResignation,DocRequestType
                    )

from OrganisationManager.serializer import CompanyPolicySerializer,AssetRequestSerializer
from calendars.models import employee_leave_request
from UserManagement .models import CustomUser


'''employee set'''
#EMPLOYEE FAMILY
class Fam_CustomFieldValueSerializer(serializers.ModelSerializer):
    def to_representation(self, instance):
        rep = super(Fam_CustomFieldValueSerializer, self).to_representation(instance)
        if instance.emp_custom_field:  # Check if emp_state_id is not None
            rep['emp_custom_field'] = instance.emp_custom_field
        return rep
    class Meta:
        model = Fam_CustomFieldValue
        fields = '__all__'
    
    def validate_field_name(self, value):
        if not EmpFamily_CustomField.objects.filter(field_name=value).exists():
            raise serializers.ValidationError(f"Field name '{value}' does not exist in Document_CustomField.")
        return value
class EmpFam_CustomFieldSerializer(serializers.ModelSerializer):
    field_values = Fam_CustomFieldValueSerializer(many=True, read_only=True)
    class Meta:
        model = EmpFamily_CustomField
        fields = '__all__'

class EmpFamSerializer(serializers.ModelSerializer):
    fam_custom_fields=Fam_CustomFieldValueSerializer(many=True, read_only=True, source='custom_field_values')
    created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    updated_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    
    class Meta:
        model= emp_family
        fields = '__all__' 
    def to_representation(self, instance):
        rep = super(EmpFamSerializer, self).to_representation(instance)
        if instance.emp_id:  # Check if emp_state_id is not None
            rep['emp_id'] = instance.emp_id.emp_first_name + " " + instance.emp_id.emp_last_name
        return rep
    
#experiance
class JobHistory_CustomFieldValueSerializer(serializers.ModelSerializer):
    def to_representation(self, instance):
        rep = super(JobHistory_CustomFieldValueSerializer, self).to_representation(instance)
        if instance.emp_custom_field:  # Check if emp_state_id is not None
            rep['emp_custom_field'] = instance.emp_custom_field
        return rep
    class Meta:
        model = JobHistory_CustomFieldValue
        fields = '__all__'
    
    def validate_field_name(self, value):
        if not EmpJobHistory_CustomField.objects.filter(field_name=value).exists():
            raise serializers.ValidationError(f"Field name '{value}' does not exist in Document_CustomField.")
        return value

class EmpJobHistory_Udf_Serializer(serializers.ModelSerializer):
    field_values = JobHistory_CustomFieldValueSerializer(many=True, read_only=True)
    class Meta:
        model = EmpJobHistory_CustomField
        fields = '__all__' 


class EmpJobHistorySerializer(serializers.ModelSerializer):
    job_history_custom_fields=JobHistory_CustomFieldValueSerializer(many=True, read_only=True, source='custom_field_values')
    created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    updated_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    class Meta:
        model= EmpJobHistory
        fields = '__all__' 
    def to_representation(self, instance):
        rep = super(EmpJobHistorySerializer, self).to_representation(instance)
        if instance.emp_id:  # Check if emp_state_id is not None
            rep['emp_id'] = instance.emp_id.emp_first_name + " " + instance.emp_id.emp_last_name
        return rep
 

#EMPLOYEE QUALIFICATION CREDENTIALS
class Qualification_CustomFieldValueSerializer(serializers.ModelSerializer):
    def to_representation(self, instance):
        rep = super(Qualification_CustomFieldValueSerializer, self).to_representation(instance)
        if instance.emp_custom_field:  # Check if emp_state_id is not None
            rep['emp_custom_field'] = instance.emp_custom_field
        return rep
    class Meta:
        model = Qualification_CustomFieldValue
        fields = '__all__'
    
    def validate_field_name(self, value):
        if not EmpQualification_CustomField.objects.filter(field_name=value).exists():
            raise serializers.ValidationError(f"Field name '{value}' does not exist in Document_CustomField.")
        return value
    
class Emp_qf_udf_Serializer(serializers.ModelSerializer):
    field_values = Qualification_CustomFieldValueSerializer(many=True, read_only=True)
    class Meta:
        model = EmpQualification_CustomField
        fields = '__all__' 

class Emp_qf_Serializer(serializers.ModelSerializer):
    qualification_fields=Qualification_CustomFieldValueSerializer(many=True, read_only=True, source='custom_field_values')
    created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    updated_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    class Meta:
        model = EmpQualification
        fields = '__all__' 
    def to_representation(self, instance):
        rep = super(Emp_qf_Serializer, self).to_representation(instance)
        if instance.emp_id:  # Check if emp_state_id is not None
            rep['emp_id'] = instance.emp_id.emp_first_name + " " + instance.emp_id.emp_last_name
        return rep
 

#EMPLOYEE DOCUMENT CREDENTIALS
class DOC_CustomFieldValueSerializer(serializers.ModelSerializer):
    # content_type_name = serializers.SerializerMethodField()
    def to_representation(self, instance):
        rep = super(DOC_CustomFieldValueSerializer, self).to_representation(instance)
        if instance.emp_custom_field:  # Check if emp_state_id is not None
            rep['emp_custom_field'] = instance.emp_custom_field
        return rep
    class Meta:
        model = Doc_CustomFieldValue
        fields = '__all__'
    
    def validate_field_name(self, value):
        if not EmpDocuments_CustomField.objects.filter(field_name=value).exists():
            raise serializers.ValidationError(f"Field name '{value}' does not exist in Document_CustomField.")
        return value
class EmpDocuments_Udf_Serializer(serializers.ModelSerializer):
    field_values = DOC_CustomFieldValueSerializer(many=True, read_only=True)
    class Meta:
        model = EmpDocuments_CustomField
        fields = '__all__'

class DocumentSerializer(serializers.ModelSerializer):
    doc_custom_fields=DOC_CustomFieldValueSerializer(many=True, read_only=True, source='custom_field_values')
    created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    updated_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    class Meta:
        model = Emp_Documents
        fields = '__all__' 
        
    def get_fields(self):
        fields = super().get_fields()
        fields['is_active'].read_only = True
        return fields
    def to_representation(self, instance):
        rep = super(DocumentSerializer, self).to_representation(instance)
        if instance.emp_id:
            rep['emp_id'] = f"{instance.emp_id.emp_first_name or ''} {instance.emp_id.emp_last_name or ''}".strip()
        # if instance.emp_id:  # Check if emp_state_id is not None
        #     rep['emp_id'] = instance.emp_id.emp_first_name + " " + instance.emp_id.emp_last_name
        if instance.document_type:
            rep['document_type'] = instance.document_type.type_name
        return rep
    def create(self, validated_data):
        # Remove any non-existent or invalid fields
        writable_fields = ['emp_id', 'emp_sl_no','document_type', 'emp_doc_number', 'emp_doc_issued_date', 'emp_doc_expiry_date', 'emp_doc_document', 'is_active']
        valid_data = {k: v for k, v in validated_data.items() if k in writable_fields}

        # Create the Emp_Documents object with valid data
        instance = Emp_Documents.objects.create(**valid_data)

        return instance
 
class DocBulkuploadSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True) 
    class Meta:
        model = Emp_Documents
        fields = '__all__'


# EMPLOYEE LEAVE REQUEST
class EmpLeaveRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmpLeaveRequest
        fields = '__all__' 
    def to_representation(self, instance):
        rep = super(EmpLeaveRequestSerializer, self).to_representation(instance)
        if instance.employee:  # Check if emp_state_id is not None
            rep['employee'] = instance.employee.emp_first_name + " " + instance.employee.emp_last_name
        
        return rep
"""employee"""


class Emp_CustomFieldValueSerializer(serializers.ModelSerializer):
    # content_type_name = serializers.SerializerMethodField()
    def to_representation(self, instance):
        rep = super(Emp_CustomFieldValueSerializer, self).to_representation(instance)
        if instance.emp_custom_field:  # Check if emp_state_id is not None
            rep['emp_custom_field'] = instance.emp_custom_field
        return rep
    class Meta:
        model = Emp_CustomFieldValue
        fields = '__all__'
    
    def validate_field_name(self, value):
        if not Emp_CustomField.objects.filter(field_name=value).exists():
            raise serializers.ValidationError(f"Field name '{value}' does not exist in Emp_CustomField.")
        return value
    
    
class CustomFieldSerializer(serializers.ModelSerializer):
    field_values = Emp_CustomFieldValueSerializer(many=True, read_only=True)
    class Meta:
        model = Emp_CustomField
        fields = '__all__' 
    
    
#emp bank details  
class EmpBankDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeBankDetail
        fields = '__all__'

class EmpBankBulkuploadSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)
    class Meta:
        model = EmployeeBankDetail
        fields = '__all__'
#Employee Skills
class EmpMarketSkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeMarketingSkill
        fields = '__all__'
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['marketing_skill'] = instance.marketing_skill.marketing if instance.marketing_skill else None
        return representation
class EmpPrgrmSkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeProgramSkill
        fields = '__all__'
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['program_skill'] = instance.program_skill.programming_language if instance.program_skill else None
        return representation
class EmpLangSkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeLangSkill
        fields = '__all__'
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['language_skill'] = instance.language_skill.language if instance.language_skill else None
        return representation
from rest_framework import serializers
from django.conf import settings
import os
import json
from .models import Report
class EmployeeReportSerializer(serializers.ModelSerializer):
    # report_data = serializers.SerializerMethodField()
    class Meta:
        model = Report
        fields = '__all__'
    # def get_report_data(self, obj):
    #     if obj.report_data:
    #         try:
    #             file_path = os.path.join(settings.MEDIA_ROOT, obj.report_data.name)
    #             with open(file_path, 'r') as f:
    #                 return json.load(f)
    #         except Exception as e:
    #             return {"error": str(e)}
    #     return None

class DocumentReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = Doc_Report
        fields = '__all__'

class GeneralReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeneralRequestReport
        fields = '__all__'

class EmployeeFilterSerializer(serializers.ModelSerializer):
    class Meta:
        model = emp_master
        fields = ['id','emp_code', 'emp_first_name', 'emp_last_name']

class ApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Approval
        fields = '__all__'

    def to_representation(self, instance):
        rep = super(ApprovalSerializer, self).to_representation(instance)
        if instance.general_request:  
            rep['general_request'] = instance.general_request.document_number
        if instance.approver:  
            rep['approver'] = instance.approver.username       
        return rep       

class LvRqstApprovalSerializer(serializers.ModelSerializer):
    from calendars.serializer import LvApprovalSerializer
    approvals = LvApprovalSerializer(many=True, read_only=True)  # Include approval details
    leave_type = serializers.SerializerMethodField()

    class Meta:
        model = employee_leave_request
        fields = ['id', 'approvals','start_date','end_date','leave_type']
    def get_leave_type(self, obj):
        # Safely return the leave type name if it exists
        return getattr(obj.leave_type, 'name', None)
        
class GeneralRequestApprovalSerializer(serializers.ModelSerializer):
    approvals = ApprovalSerializer(many=True, read_only=True)  # Include approval details

    class Meta:
        model = GeneralRequest
        fields = ['id', 'approvals']
        # fields = ['id', 'doc_number', 'reason', 'status', 'created_at_date', 'approvals']

#EMPLOYEE SERIALIZER
class EmpSerializer(serializers.ModelSerializer):
    # payslip  = PayslipSerializer(many=True, read_only=True, source='payslips')
    emp_bank = EmpBankDetailsSerializer(many=True,read_only=True, source='bank_details')
    advance_salary_requests   =  AdvanceSalaryRequestSerializer(many=True, read_only=True)
    loan_requests   =  LoanApplicationSerializer(many=True, read_only=True, source='loan')
    assets_requests   =  AssetRequestSerializer(many=True, read_only=True, source='asset_requests')
    requests = GeneralRequestApprovalSerializer(many=True, read_only=True, source='generalrequest_set')
    leave_rqsts = LvRqstApprovalSerializer(many=True, read_only=True, source='employee_leave_request_set')
    leave_balance = EmployeeLeaveBalanceSerializer(many=True, read_only=True, source='emp_leave_balance_set')
    custom_fields = Emp_CustomFieldValueSerializer(many=True, read_only=True, source='custom_field_values')
    emp_family = EmpFamSerializer(many=True, read_only=True)
    emp_documents = DocumentSerializer(many=True, read_only=True)
    emp_qualification = Emp_qf_Serializer(many=True, read_only=True)
    emp_job_history = EmpJobHistorySerializer(many=True, read_only=True)
    emp_market_skills = EmpMarketSkillSerializer(many=True, read_only=True)
    emp_prgrm_skills = EmpPrgrmSkillSerializer(many=True, read_only=True)
    emp_lang_skills= EmpLangSkillSerializer(many=True, read_only=True)
    policy_file = CompanyPolicySerializer(many=True, read_only=True)
    emp_weekend_calendar = WeekendCalendarSerailizer(required=False, read_only=True)
    holiday_calendar = HolidayCalandarSerializer(required=False, read_only=True)
    
    
    # created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    # updated_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    class Meta:
        model = emp_master
        fields = '__all__' 
    def create(self, validated_data):
        validated_data['is_active'] = True  # Force is_active to True
        return super().create(validated_data)
    def to_representation(self, instance):
        rep = super(EmpSerializer, self).to_representation(instance)
        if instance.emp_state_id:  # Check if emp_state_id is not None
            rep['emp_state_id'] = instance.emp_state_id.state_name
        if instance.emp_country_id:  
            rep['emp_country_id'] = instance.emp_country_id.country_name
        if instance.emp_desgntn_id:  
            rep['emp_desgntn_id'] = instance.emp_desgntn_id.desgntn_job_title
        if instance.emp_dept_id:  
            rep['emp_dept_id'] = instance.emp_dept_id.dept_name
        if instance.emp_ctgry_id:
            rep['emp_ctgry_id'] =instance.emp_ctgry_id.ctgry_title
        if instance.emp_branch_id:
            rep['emp_branch_id'] =instance.emp_branch_id.branch_name
        if instance.emp_nationality:
            rep['emp_nationality'] =instance.emp_nationality.N_name
        if instance.emp_relegion:
            rep['emp_relegion'] =instance.emp_relegion.religion
        return rep
        
    def get_holidays(self, obj):
        holidays = holiday.objects.filter(holiday_calendar=obj.holiday_calendar)
        return HolidaySerializer(holidays, many=True).data
class EmplistSerializer(serializers.ModelSerializer):
    class Meta:
        model = emp_master
        fields = ['emp_code', 'emp_first_name', 'emp_last_name', 'emp_profile_pic','id','is_active']

class EmpBulkUploadSerializer(serializers.ModelSerializer):
    emp_custom_fields = CustomFieldSerializer(many=True, required=False)
    file = serializers.FileField(write_only=True) 
    class Meta:
        model = emp_master
        fields = '__all__'

    def create(self, validated_data):
        custom_fields_data = validated_data.pop('emp_custom_fields', [])
        file=validated_data.pop('file', None)
        instance = super().create(validated_data)
        for custom_field_data in custom_fields_data:
            Emp_CustomField.objects.create(emp_master=instance, **custom_field_data)
        return instance

class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = notification
        fields = '__all__'


class NotSerializer(serializers.ModelSerializer):
    class Meta:
        model = notification
        fields = '__all__'

class RequestTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RequestType
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(RequestTypeSerializer, self).to_representation(instance)
        if instance.salary_component:  # Check if emp_state_id is not None
            rep['salary_component'] = instance.salary_component.name
        return rep
class EmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailTemplate
        fields = '__all__'

class ReqNotifySerializer(serializers.ModelSerializer):
    class Meta:
        model = RequestNotification
        fields = '__all__'
    
    def to_representation(self, instance):
        rep = super(ReqNotifySerializer, self).to_representation(instance)
        rep['recipient_user'] = instance.recipient_user.username if instance.recipient_user else None
        rep['recipient_employee'] = instance.recipient_employee.emp_first_name if instance.recipient_employee else None
        # rep['approval'] = instance.approval.id if instance.approval else None
        return rep


class EmailConfigurationSerializer(serializers.ModelSerializer):
    email_host_password = serializers.CharField(write_only=True, required=False)
    class Meta:
        model = EmailConfiguration
        fields = '__all__'
    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Mask the password field
        data['email_host_password'] = '********' if instance.email_host_password else ''
        return data
class CommonWorkflowSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommonWorkflow
        fields = '__all__'

class GeneralRequestSerializer(serializers.ModelSerializer):
    approvals = ApprovalSerializer(many=True, read_only=True)
    document_numbering_details = serializers.SerializerMethodField()

    class Meta:
        model = GeneralRequest
        fields = '__all__'

    def get_document_numbering_details(self, obj):
        return {
            "document_number": obj.document_number,
            "prefix": obj.document_number.split('-')[0] if obj.document_number else None,
            # "year": obj.document_number.split('-')[1] if obj.document_number else None,
        }
    def to_representation(self, instance):
        rep = super(GeneralRequestSerializer, self).to_representation(instance)
        if instance.employee:  
            rep['employee'] = instance.employee.emp_first_name
        if instance.request_type:  
            rep['request_type'] = instance.request_type.name
        return rep

class ApprovalLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApprovalLevel
        fields = '__all__'
    def validate(self, attrs):
        level = attrs.get('level')
        request_type = attrs.get('request_type')
        branches = attrs.get('branch')  # This will be a list of branches

        for branch in branches:
            if ApprovalLevel.objects.filter(
                level=level,
                request_type=request_type,
                branch=branch
            ).exists():
                raise serializers.ValidationError(
                    f"An approval level with level={level} already exists for branch '{branch}' and request type '{request_type}'."
                )

        return attrs
    def to_representation(self, instance):
        rep = super(ApprovalLevelSerializer, self).to_representation(instance)
        if instance.request_type:  
            rep['request_type'] = instance.request_type.name
        if instance.approver:  
            rep['approver'] = instance.approver.username
        if instance.branch.exists():  
            rep['branch'] = [cat.branch_name for cat in instance.branch.all()]
        return rep
    

class SelectedEmpNotifySerializer(serializers.ModelSerializer):
    class Meta:
        model = SelectedEmpNotify
        fields = '__all__'

class NotificationSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationSettings
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(NotificationSettingsSerializer, self).to_representation(instance)
        if instance.branch:  
            rep['branch'] = instance.branch.branch_name
        if instance.notify_users.exists():  
            rep['notify_users'] = [cat.username for cat in instance.notify_users.all()]

        return rep
class DocExpEmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocExpEmailTemplate
        fields = '__all__'
class DocRequestTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocRequestType
        fields = '__all__'
class DocRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentRequest
        fields = '__all__'

class DocApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentApproval
        fields = '__all__'

class DocApprovalLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentApprovalLevel
        fields = '__all__'

class ResignationApprovalLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResignationApprovalLevel
        fields = '__all__'
class ResignationApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResignationApproval
        fields = '__all__'
class DocRequestEmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocRequestEmailTemplate
        fields = '__all__'
class DocRequestNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocRequestNotification
        fields = '__all__'
class EmployeeResignationSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeResignation
        fields = '__all__'
class EndOfServiceSerializer(serializers.ModelSerializer):
    employee_code = serializers.CharField(source='resignation.employee.emp_code', read_only=True)
    employee_name = serializers.CharField(source='resignation.employee.emp_first_name', read_only=True)
    designation = serializers.CharField(source='resignation.employee.emp_desgntn_id.desgntn_job_title', read_only=True)  # Assume designation has name
    department = serializers.CharField(source='resignation.employee.emp_dept_id.dept_name', read_only=True)  # Assume department has name
    work_status = serializers.SerializerMethodField()
    basic_salary = serializers.SerializerMethodField()
    per_day_gratuity = serializers.SerializerMethodField()
    final_month_salary = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    last_payroll_processed = serializers.SerializerMethodField()
    asset_return_pending = serializers.SerializerMethodField()

    class Meta:
        model = EndOfService
        fields = [
            'id','employee_code', 'employee_name', 'designation', 'department',
            'date_of_joining', 'date_of_resignation_termination', 'last_working_date',
            'notice_period_days', 'total_service_days', 'net_number_of_days_worked',
            'leave_days_without_pay', 'leave_balance', 'last_month_salary',
            'gratuity_days', 'gratuity_amount', 'notice_pay', 'status', 'processed_date','basic_salary','work_status',
            'per_day_gratuity','air_ticket','final_month_salary','last_payroll_processed','asset_return_pending'

        ]
        # fields = '__all__'
    def get_work_status(self, obj):
        return obj.resignation.get_termination_type_display()

    def get_basic_salary(self, obj):
        from PayrollManagement.models import EmployeeSalaryStructure,Payslip
        component = EmployeeSalaryStructure.objects.filter(
            employee=obj.resignation.employee,
            component__is_gratuity=True,
            is_active=True
        ).order_by('-date_updated').first()
        return component.amount if component else Decimal('0.00')
    def get_asset_return_pending(self, obj):
        employee = obj.resignation.employee
        return AssetAllocation.objects.filter(
            employee=employee,
            returned_date__isnull=True
        ).exists()
    def get_per_day_gratuity(self, obj):
        basic = self.get_basic_salary(obj)
        return round(basic / 30, 2) if basic else 0.0
    def get_last_payroll_processed(self, obj):
        employee = obj.resignation.employee
        latest_payslip = employee.payslips.filter(
            status__in=['paid', 'Approved']
        ).order_by(
            '-payroll_run__year', '-payroll_run__month'
        ).first()

        if latest_payslip and latest_payslip.payroll_run:
            month = month_name[latest_payslip.payroll_run.month]
            year = latest_payslip.payroll_run.year
            return f"{month} {year}"
        return None
