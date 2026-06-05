
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
from PayrollManagement .serializer import AdvanceSalaryRequestSerializer,LoanApplicationSerializer,PayslipSerializer,AirTicketRequestSerializer
from decimal import Decimal
from calendar import month_name
from django.utils import timezone
from django.db import models  # Ensure models import is included


from .models import (emp_family,EmpJobHistory,EmpQualification,Emp_Documents,EmpLeaveRequest,emp_master,Emp_CustomField,
                    EmpFamily_CustomField,EmpJobHistory_CustomField,EmpQualification_CustomField,EmpDocuments_CustomField,
                    notification,Report,Doc_Report,RequestType,
                    GeneralRequest,GeneralRequestReport,EmployeeMarketingSkill,EmployeeProgramSkill,EmployeeLangSkill,Approval,
                    ApprovalLevel,RequestNotification,Emp_CustomFieldValue,EmailTemplate,EmailConfiguration,SelectedEmpNotify,NotificationSettings,
                    DocExpEmailTemplate,CommonWorkflow,Doc_CustomFieldValue,EmployeeBankDetail,Fam_CustomFieldValue,Qualification_CustomFieldValue,
                    JobHistory_CustomFieldValue,DocumentRequest,DocumentApprovalLevel,DocumentApproval,ResignationApprovalLevel,ResignationApproval,DocRequestEmailTemplate,
                    DocRequestNotification,EndOfService,EmployeeResignation,DocRequestType,ResignationEmailTemplate,ResignationRequestNotification,ApprovalWorkflow,DocumentApprovalWorkflow,ResignationApprovalWorkflow,document_type
                    )

from OrganisationManager.serializer import CompanyPolicySerializer,AssetRequestSerializer
from calendars.models import employee_leave_request,assign_holiday
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
 
class Document_typeSerializer(serializers.ModelSerializer):
    class Meta:
        model = document_type
        fields = '__all__'
 

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
        fields = ['id', 'approvals','start_date','end_date','leave_type','reason','document_number']
    def get_leave_type(self, obj):
        # Safely return the leave type name if it exists
        return getattr(obj.leave_type, 'name', None)
        
class GeneralRequestApprovalSerializer(serializers.ModelSerializer):
    approvals = ApprovalSerializer(many=True, read_only=True)  # Include approval details

    class Meta:
        model = GeneralRequest
        fields = ['id', 'approvals','document_number', 'reason', 'status', 'created_at_date','request_type','remarks']
        # fields = ['id', 'doc_number', 'reason', 'status', 'created_at_date', 'approvals']
class DocRequestSerializer(serializers.ModelSerializer):
    document_numbering_details = serializers.SerializerMethodField()
    class Meta:
        model = DocumentRequest
        fields = '__all__'
    def get_document_numbering_details(self, obj):
        return {
            "document_number": obj.document_number,
            "prefix": obj.document_number.split('-')[0] if obj.document_number else None,
            # "year": obj.document_number.split('-')[1] if obj.document_number else None,
        }
    
    def to_representation(self, instance):
        rep = super(DocRequestSerializer, self).to_representation(instance)
        if instance.request_type:  
            rep['request_type'] = instance.request_type.type_name
        if instance.employee:  
            rep['employee'] = instance.employee.emp_first_name
        if instance.branch:
            rep['branch']=instance.branch.branch_name
        
        return rep
    def validate(self, data):

        employee = data.get('employee')
        request_type = data.get('request_type')

        if employee and request_type:

            # 🔥 FIX: correct M2M branch filtering
            workflow = DocumentApprovalWorkflow.objects.filter(
                request_type=request_type,
                branch__in=[employee.emp_branch_id]
            ).first()

            if not workflow:
                return data

            # ✅ Step 2: Get first level
            first_level = workflow.document_levels.order_by('level').first()

            # ✅ Step 3: Check approval type (from workflow)
            if workflow.approval_type == 'reporting_manager':

                if not employee.emp_reporting_manager:
                    raise serializers.ValidationError(
                        "Employee does not have a reporting manager configured."
                    )

        return data


class EmployeeResignationSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeResignation
        fields = '__all__'
    
#EMPLOYEE SERIALIZER
class EmpSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)
    announcements = serializers.SerializerMethodField()
    projects = serializers.SerializerMethodField() 
    airticket_request      =  AirTicketRequestSerializer(many=True,read_only=True,source='airticket_requests')
    document_requests      = DocRequestSerializer(many=True, read_only=True)
    payslip  = PayslipSerializer(many=True, read_only=True, source='payslips')
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
    holidays = serializers.SerializerMethodField()
    branch                 = serializers.SerializerMethodField()
    resignation_requests   =  EmployeeResignationSerializer(many=True, read_only=True, source='resignations')
    
    
    
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
        if instance.emp_reporting_manager:
            rep['emp_reporting_manager'] =instance.emp_reporting_manager.username
        return rep
    def update(self, instance, validated_data):
        if 'is_active' not in validated_data:
            validated_data['is_active'] = instance.is_active
        return super().update(instance, validated_data)        
    
    def get_holidays(self, obj):
        from calendars.serializer import HolidaySerializer

        # Collect all applicable holiday calendars for this employee
        holiday_calendars = set()

        # 1️⃣ Branch-level holidays
        if obj.emp_branch_id:
            holiday_calendars.update(
                assign_holiday.objects.filter(
                    related_to="branch",
                    branch=obj.emp_branch_id
                ).values_list("holiday_model", flat=True)
            )

        # 2️⃣ Department-level holidays
        if obj.emp_dept_id:
            holiday_calendars.update(
                assign_holiday.objects.filter(
                    related_to="department",
                    department=obj.emp_dept_id
                ).values_list("holiday_model", flat=True)
            )

        # 3️⃣ Category-level holidays
        if obj.emp_ctgry_id:
            holiday_calendars.update(
                assign_holiday.objects.filter(
                    related_to="category",
                    category=obj.emp_ctgry_id
                ).values_list("holiday_model", flat=True)
            )

        # 4️⃣ Employee-level holidays
        holiday_calendars.update(
            assign_holiday.objects.filter(
                related_to="employee",
                employee=obj
            ).values_list("holiday_model", flat=True)
        )

        # 5️⃣ Now fetch all holidays from these calendars
        holidays = holiday.objects.filter(calendar__in=holiday_calendars)
        return HolidaySerializer(holidays, many=True).data
    def get_announcements(self, obj):
        from OrganisationManager .models import Announcement
        now = timezone.now()
        # Announcements directly assigned to employee
        direct = Announcement.objects.filter(
            specific_employees=obj
        )
        # Announcements assigned to employee branch
        branch_ann = Announcement.objects.filter(
            branches=obj.emp_branch_id
        )
        # Combine & remove duplicates
        announcements = (direct | branch_ann).distinct()

        #Exclude expired or not yet active announcements
        announcements = announcements.filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gte=now),
            models.Q(schedule_at__isnull=True) | models.Q(schedule_at__lte=now)
        )
        return announcements.values(
            "id", "title", "message", "created_at", "is_sticky",
            "allow_comments", "attachment","schedule_at","expires_at"
        )
    def get_projects(self, obj):
        from ProjectManagement .serializer import ProjectSerializer  # to avoid circular import
        from ProjectManagement .models import Project

        projects = Project.objects.filter(
            models.Q(managers=obj) | models.Q(members=obj)
        ).distinct()
        return ProjectSerializer(projects, many=True).data
    def get_branch(self, obj):
        if obj.emp_branch_id:
            return {
                "id": obj.emp_branch_id.id,
                "name": obj.emp_branch_id.branch_name
            }
        return None 
class EmplistSerializer(serializers.ModelSerializer):
    class Meta:
        model = emp_master
        fields = ['emp_code', 'emp_first_name', 'emp_last_name', 'emp_profile_pic','id','is_active','emp_branch_id',
                  'emp_dept_id','emp_desgntn_id','emp_ctgry_id']
    def to_representation(self, instance):
        rep = super(EmplistSerializer, self).to_representation(instance)
        if instance.emp_desgntn_id:  
            rep['emp_desgntn_id'] = instance.emp_desgntn_id.desgntn_job_title
        if instance.emp_dept_id:  
            rep['emp_dept_id'] = instance.emp_dept_id.dept_name
        if instance.emp_ctgry_id:
            rep['emp_ctgry_id'] =instance.emp_ctgry_id.ctgry_title
        if instance.emp_branch_id:
            rep['emp_branch_id'] =instance.emp_branch_id.branch_name
        return rep
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
        if instance.branch:
           rep['branch'] = [branch.branch_name for branch in instance.branch.all()]
        return rep
class EmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailTemplate
        fields = '__all__'
    def validate(self, attrs):
        template_type=attrs.get("template_type")
        temp=EmailTemplate.objects.filter(template_type=template_type)
        if self.instance:
            temp=temp.exclude(id=self.instance.id)
        if temp.exists():
            raise serializers.ValidationError({"template_name": f"{template_type} template already exists."
        })
        return attrs


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
        if instance.branch:
            rep['branch']=instance.branch.branch_name
        return rep
    def validate(self, data):
        request_type = data.get("request_type")
        employee = data.get("employee")

        if request_type.use_common_workflow:
            first_level = CommonWorkflow.objects.order_by("level").first()

            if not first_level:
                raise serializers.ValidationError({
                    "approval": "Approval levels are not configured."
                })

            approval_type = first_level.approval_type

        else:
            workflow = ApprovalWorkflow.objects.filter(
                request_type=request_type,
                branch__in=[employee.emp_branch_id]
            ).first()

            if not workflow:
                workflow = ApprovalWorkflow.objects.filter(
                    request_type=request_type
                ).first()

            if not workflow:
                raise serializers.ValidationError({
                    "approval": "Approval workflow is not configured."
                })

            first_level = workflow.levels.order_by("level").first()
            if not first_level:
                first_level = ApprovalLevel.objects.create(
                    workflow=workflow,
                    level=1,
                    role="Auto Level",
                    approver=None
                )

            approval_type = workflow.approval_type

        if approval_type == "reporting_manager" and not employee.emp_reporting_manager:
            raise serializers.ValidationError({
                "reporting_manager": "Employee has no reporting manager."
            })

        levels = self.initial_data.get("levels", [])
        cleaned_levels = []

        for level in levels:
            approver = level.get("approver")
            level_type = level.get("approval_type")

            if approver == 0:
                approver = None

            if level_type in ["no_approval", "reporting_manager"]:
                approver = None

            if level_type == "multi_approval" and not approver:
                raise serializers.ValidationError({
                    "approver": "Approver is required for multi approval level."
                })

            level["approver"] = approver
            cleaned_levels.append(level)

        self._validated_levels = cleaned_levels

        return data
        
        
    
class ApprovalLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApprovalLevel
        fields = ['id', 'level', 'role', 'approver', 'escalate_to', 'escalate_after_days', 'escalate_after_hours', 'escalate_after_minutes']
    def to_representation(self, instance):
        rep = super().to_representation(instance)
        if instance.approver:
            rep['approver'] = instance.approver.username
        return rep


class ApprovalWorkflowSerializer(serializers.ModelSerializer):
    levels = ApprovalLevelSerializer(many=True)
    # created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = ApprovalWorkflow
        fields = '__all__'

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        if instance.request_type:
            rep['request_type'] = instance.request_type.name
        if instance.branch.exists():
            rep['branch'] = [b.branch_name for b in instance.branch.all()]
        return rep

    def create(self, validated_data):
        levels_data = validated_data.pop('levels', [])
        branches = validated_data.pop('branch', [])

        workflow = ApprovalWorkflow.objects.create(**validated_data)
        workflow.branch.set(branches)

        for level_data in levels_data:
            ApprovalLevel.objects.create(workflow=workflow, **level_data)

        return workflow

    def update(self, instance, validated_data):
        levels_data = validated_data.pop('levels', None)
        branches = validated_data.pop('branch', None)

        instance.request_type = validated_data.get('request_type', instance.request_type)
        instance.approval_type = validated_data.get('approval_type', instance.approval_type)
        instance.save()

        if branches is not None:
            instance.branch.set(branches)

        if levels_data is not None:
            instance.levels.all().delete()

            # ✅ ONLY ADD THIS CONDITION
            if instance.approval_type == 'multi_approval':
                for level_data in levels_data:
                    ApprovalLevel.objects.create(workflow=instance, **level_data)

        return instance
    

class SelectedEmpNotifySerializer(serializers.ModelSerializer):
    class Meta:
        model = SelectedEmpNotify
        fields = '__all__'

class NotificationSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationSettings
        fields = '__all__'
    def validate_branch(self, value):
        # Exclude current instance when updating
        instance = getattr(self, 'instance', None)
        if NotificationSettings.objects.filter(branch=value).exclude(id=instance.id if instance else None).exists():
            raise serializers.ValidationError(
                "Notification settings for this branch already exist."
            )
        return value
    def to_representation(self, instance):
        rep = super().to_representation(instance)

        rep['branch'] = [
            branch.branch_name
            for branch in instance.branch.all()
        ]

        rep['notify_users'] = [
            user.username
            for user in instance.notify_users.all()
        ]

        return rep
class DocExpEmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocExpEmailTemplate
        fields = '__all__'
    def validate(self, attrs):
        template_name=attrs.get("template_name")
        temp=DocExpEmailTemplate.objects.filter(template_name=template_name)
        if self.instance:
            temp=temp.exclude(id=self.instance.id)
        if temp.exists():
            raise serializers.ValidationError({"template_name": f"{template_name} template already exists."
        })
        return attrs

class DocRequestTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocRequestType
        fields = '__all__'

class DocApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentApproval
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(DocApprovalSerializer, self).to_representation(instance)
        if instance.approver:  
            rep['approver'] = instance.approver.username
        if instance.general_request:
            rep['document_request'] = instance.document_request.document_number
        return rep
    
class DocApprovalLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentApprovalLevel
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(DocApprovalLevelSerializer, self).to_representation(instance)
        if instance.approver:  
            rep['approver'] = instance.approver.username
    
class DocumentApprovalWorkflowSerializer(serializers.ModelSerializer):
    levels = DocApprovalLevelSerializer(source='document_levels', many=True)

    class Meta:
        model = DocumentApprovalWorkflow
        fields = '__all__'

    def to_representation(self, instance):
        rep = super(DocumentApprovalWorkflowSerializer, self).to_representation(instance)
        if instance.request_type:  
            rep['request_type'] = instance.request_type.type_name
        if instance.branch:
           rep['branch'] = [branch.branch_name for branch in instance.branch.all()]
        return rep

    def create(self, validated_data):
        # ✅ FIX KEY
        levels_data = validated_data.pop('document_levels', [])
        branches = validated_data.pop('branch', [])

        workflow = DocumentApprovalWorkflow.objects.create(**validated_data)
        workflow.branch.set(branches)

        for level_data in levels_data:
            level_data.pop('workflow', None)  # safety
            DocumentApprovalLevel.objects.create(
                workflow=workflow,
                **level_data
            )

        return workflow

    def update(self, instance, validated_data):
        # ✅ FIX KEY
        levels_data = validated_data.pop('document_levels', None)
        branches = validated_data.pop('branch', None)

        instance.request_type = validated_data.get('request_type', instance.request_type)
        instance.approval_type = validated_data.get('approval_type', instance.approval_type)
        instance.save()

        if branches is not None:
            instance.branch.set(branches)

        if levels_data is not None:
            # ✅ FIX RELATION NAME
            instance.document_levels.all().delete()

            for level_data in levels_data:
                level_data.pop('workflow', None)
                DocumentApprovalLevel.objects.create(
                    workflow=instance,
                    **level_data
                )

        return instance
    
class ResignationApprovalLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResignationApprovalLevel
        fields = '__all__'
    def to_representation(self, instance):
        rep = super().to_representation(instance)

        if not instance.role:
            if instance.workflow.approval_type == 'reporting_manager':
                rep['role'] = "Reporting Manager"
            elif instance.workflow.approval_type == 'no_approval':
                rep['role'] = "Auto Approval"
            else:
                rep['role'] = "Approver"

        if instance.workflow.approval_type == 'reporting_manager':
            employee = self.context.get('employee')

            if employee and getattr(employee, 'emp_reporting_manager', None):
                rep['approver'] = employee.emp_reporting_manager.username
            else:
                rep['approver'] = None

        elif instance.approver:
            rep['approver'] = instance.approver.username

        return rep
        
class ResignationApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResignationApproval
        fields = '__all__'

    def to_representation(self, instance):
        rep = super().to_representation(instance)

        
        if instance.resignation_request:
            rep['resignation_request'] = instance.resignation_request.document_number

        if instance.approver:
            rep['approver'] = instance.approver.username

        return rep
        
        
class ResignationApprovalWorkflowSerializer(serializers.ModelSerializer):
    levels = ResignationApprovalLevelSerializer(many=True,source='resignation_levels')
    # created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = ResignationApprovalWorkflow
        fields = '__all__'

    def to_representation(self, instance):

        instance = ResignationApprovalWorkflow.objects.prefetch_related(
            'resignation_levels'
        ).get(id=instance.id)

        rep = super().to_representation(instance)

        rep['levels'] = ResignationApprovalLevelSerializer(
            instance.resignation_levels.all().order_by('level'),
            many=True,
            context=self.context
        ).data

        rep['branch'] = [
            b.branch_name for b in instance.branch.all()
        ] if instance.branch.exists() else []

        return rep

    def create(self, validated_data):
        levels_data = validated_data.pop('levels', None)
        if levels_data is None:
            levels_data = validated_data.pop('resignation_levels', [])

        branches = validated_data.pop('branch', [])

        workflow = ResignationApprovalWorkflow.objects.create(**validated_data)
        if branches:
            workflow.branch.set(branches)

        for level_data in levels_data:
            level_data.pop('workflow', None)  # safety
            ResignationApprovalLevel.objects.create(
                workflow=workflow,
                **level_data
            )

        return workflow

    def update(self, instance, validated_data):
        levels_data = validated_data.pop('resignation_levels', None)
        branches = validated_data.pop('branch', None)

        # ---------------- UPDATE MAIN FIELDS ---------------- #
        instance.approval_type = validated_data.get(
            'approval_type',
            instance.approval_type
        )
        instance.save()

        # ---------------- UPDATE BRANCH ---------------- #
        if branches is not None:
            instance.branch.set(branches)

        # ---------------- UPDATE LEVELS ---------------- #
        if levels_data is not None:
            instance.resignation_levels.all().delete()

            if instance.approval_type == 'multi_approval':

                if not levels_data:
                    raise serializers.ValidationError({
                        "levels": "At least one level is required for multi approval"
                    })

                for index, level_data in enumerate(levels_data, start=1):

                    level_data.pop('workflow', None)
                    level_data.pop('id', None)
                    level_data['level'] = index

                    ResignationApprovalLevel.objects.create(
                        workflow=instance,
                        **level_data
                    )

        return instance
    
    
        
        
class ResignationTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResignationEmailTemplate
        fields = '__all__'
    def validate(self, attrs):
        template_type=attrs.get("template_type")
        temp=ResignationEmailTemplate.objects.filter(template_type=template_type)
        if self.instance:
            temp=temp.exclude(id=self.instance.id)
        if temp.exists():
            raise serializers.ValidationError({"template_type": f"{template_type} template already exists."
        })
        return attrs

    def to_representation(self, instance):
            rep = super( EmployeeResignationSerializer, self).to_representation(instance)
            if instance.employee:  
                rep['employee'] = instance.employee.emp_first_name
            return rep
class DocRequestEmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocRequestEmailTemplate
        fields = '__all__'
    def validate(self, attrs):
        template_type=attrs.get("template_type")
        temp=DocRequestEmailTemplate.objects.filter(template_type=template_type)
        if self.instance:
            temp=temp.exclude(id=self.instance.id)
        if temp.exists():
            raise serializers.ValidationError({"template_name": f"{template_type} template already exists."
        })
        return attrs
class DocRequestNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocRequestNotification
        fields = '__all__'
class EmployeeResignationSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeResignation
        fields = '__all__'
    def validate(self, data):
        employee = data.get('employee')

        if not employee:
            raise serializers.ValidationError({
                "employee": "Employee is required."
            })

        # ✅ FIX 1: correct ManyToMany filtering + stable selection
        workflow = ResignationApprovalWorkflow.objects.filter(
           branch=employee.emp_branch_id 
        ).order_by('-id').first()

        if not workflow:
            raise serializers.ValidationError({
                "workflow": "Resignation approval workflow is not configured for this branch."
            })

        # ✅ Reporting manager check
        if workflow.approval_type == 'reporting_manager':
            if not employee.emp_reporting_manager:
                raise serializers.ValidationError({
                    "employee": "This employee does not have a reporting manager assigned."
                })

        # ✅ Multi approval check
        if workflow.approval_type == 'multi_approval':
            if not workflow.resignation_levels.exists():
                raise serializers.ValidationError({
                    "approval_level": "Approval levels are not configured for this workflow."
                })

        # ✅ FIX 2: handle update case
        qs = EmployeeResignation.objects.filter(
            employee=employee,
            status__in=['Pending', 'Approved']
        )

        if self.instance:
            qs = qs.exclude(id=self.instance.id)

        if qs.exists():
            raise serializers.ValidationError(
                "You already have an active resignation request."
            )

        return data

    def to_representation(self, instance):
        rep = super().to_representation(instance)

        # ✅ employee code
        if instance.employee:
            rep['employee'] = instance.employee.emp_code
        if instance.branch:
            rep['branch']=instance.branch.branch_name

        # ✅ get current (latest) approval
        latest_approval = instance.resign_approvals.order_by('-level').first()

        rep['approver'] = (
            latest_approval.approver.id
            if latest_approval and latest_approval.approver
            else None
        )

        return rep

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
class EscalationRuleSerializer(serializers.ModelSerializer):
    request_type = serializers.CharField(source='workflow.request_type.name',read_only=True)
    approver_name = serializers.CharField(source='approver.username',read_only=True)
    escalate_to_name = serializers.CharField(source='escalate_to.username',read_only=True)
    branch = serializers.PrimaryKeyRelatedField(source='workflow.branch',many=True,read_only=True)
    class Meta:
        model = ApprovalLevel
        fields = [
            'id',
            'level',
            'role',
            'request_type',
            'branch',
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
            'escalate_to',
            'request_type',
            'branch',
            'approver_name',
            'escalate_to_name'
        ]
class ResignationRequestNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResignationRequestNotification
        fields = '__all__'
