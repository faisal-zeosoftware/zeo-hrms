from .models import (brnch_mstr,dept_master,desgntn_master,DocumentNumbering,
                     ctgry_master,FiscalPeriod,FiscalYear,CompanyPolicy,
                     Announcement,AnnouncementView,AnnouncementComment,Asset,AssetAllocation,AssetType, AssetRequest,AssetCustomField,AssetReport,
                     AssetCustomFieldValue,AssetTransactionReport,GratuityTable,Folder, Document,AssetEmailTemplate,AssetApprovalLevel,AssetApproval)
from rest_framework import serializers
from tenant_users.tenants.models import UserTenantPermissions
from django.contrib.auth.models import Permission,Group
from calendars .models import assign_holiday,holiday,holiday_calendar
from django.utils import timezone
from ProjectManagement .serializer import ProjectSerializer
from django.db import models

class CompanyPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = CompanyPolicy
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(CompanyPolicySerializer, self).to_representation(instance)
        if instance.branch:
            rep['branch'] =instance.branch.branch_name
        if instance.department:
            rep['department'] =instance.department.dept_name
        if instance.category:
            rep['category'] =instance.category.ctgry_title
        return rep

    
#DEPARTMENT SERIALIZER
class DeptSerializer(serializers.ModelSerializer):
    # dept_created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    # dept_updated_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    class Meta:
        model = dept_master
        fields= '__all__'
    def to_representation(self, instance):
        rep = super(DeptSerializer, self).to_representation(instance)
        if instance.branch_id:
            rep['branch_id'] =instance.branch_id.branch_name
        return rep
class DeptUploadSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)
    class Meta:
        model = dept_master
        fields= '__all__'

#DESIGNATION SERIALIZER
class DesgSerializer(serializers.ModelSerializer):
    desgntn_created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    desgntn_updated_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    class Meta:
        model = desgntn_master
        fields= '__all__'
class DesgUploadSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)
    class Meta:
        model = desgntn_master
        fields= '__all__'




#CATOGARY SERIALIZER
class CtgrySerializer(serializers.ModelSerializer):
    ctgry_created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    ctgry_updated_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    class Meta:
        model = ctgry_master
        fields= '__all__'
#CATEGARY Bulupload SERIALIZER
class CtgryUploadSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)
    class Meta:
        model = ctgry_master
        fields= '__all__'

class FiscalYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = FiscalYear
        fields = '__all__'

class PeriodSerializer(serializers.ModelSerializer):
    class Meta:
        model = FiscalPeriod
        fields = '__all__'

class permserializer(serializers.ModelSerializer):
    class Meta:
        model=Permission
        fields=['id','codename']

class GroupSerializer(serializers.ModelSerializer):
    permissions = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Permission.objects.all()
    )
    class Meta:
        model = Group
        fields='__all__'
    
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['permissions'] = permserializer(instance.permissions.all(), many=True).data
        return representation

class PermissionSerializer(serializers.ModelSerializer):
    user = serializers.CharField(source="profile.username", read_only=True)
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['groups'] = GroupSerializer(instance.groups.all(), many=True).data
        representation['user_permissions'] = permserializer(instance.user_permissions.all(), many=True).data
        return representation

    class Meta:
        model = UserTenantPermissions
        fields = '__all__'


class DocumentNumberingSerializer(serializers.ModelSerializer):
    
    class Meta:
        model = DocumentNumbering
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(DocumentNumberingSerializer, self).to_representation(instance)
        if instance.branch_id:  # Check if emp_state_id is not None
            rep['branch_id'] = instance.branch_id.branch_name
        # if instance.category:  # Check if emp_state_id is not None
        #     rep['category'] = instance.category.ctgry_title
        return rep
    
class AnnouncementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Announcement
        fields = '__all__'
class AnnouncementViewSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnnouncementView
        fields = '__all__'
class AnnouncementCommentSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.first_name', read_only=True)
    class Meta:
        model = AnnouncementComment
        fields = ['id', 'announcement', 'employee', 'comment', 'created_at', 'employee_name']

class AssetCustomFieldValueSerializer(serializers.ModelSerializer):
    def to_representation(self, instance):
        rep = super(AssetCustomFieldValueSerializer, self).to_representation(instance)
        if instance.asset:  # Check if emp_state_id is not None
            rep['asset'] = instance.asset.name
        if instance.custom_field:  # Check if emp_state_id is not None
            rep['custom_field'] = instance.custom_field.custom_field
        return rep
    class Meta:
        model = AssetCustomFieldValue
        fields = '__all__'   

class AssetCustomFieldSerializer(serializers.ModelSerializer):
    field_values = AssetCustomFieldValueSerializer(many=True, read_only=True)
    class Meta:
        model = AssetCustomField
        fields = '__all__'
class AssetTypeSerializer(serializers.ModelSerializer):
    asset_custom_fields=AssetCustomFieldValueSerializer(many=True, read_only=True, source='field_values')
    class Meta:
        model = AssetType
        fields = '__all__'
class AssetSerializer(serializers.ModelSerializer):
    asset_custom_fields=AssetCustomFieldValueSerializer(many=True, read_only=True, source='custom_field_values')
    class Meta:
        model = Asset
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(AssetSerializer, self).to_representation(instance)
        if instance.asset_type:
            rep['asset_type'] =instance.asset_type.name
        return rep
    

class AssetApprovalLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetApprovalLevel
        fields = '__all__'
    def validate(self, attrs):
        level = attrs.get('level')
        asset_type = attrs.get('asset_type')
        branches = attrs.get('branch')  # This will be a list of branches

        for branch in branches:
            if AssetApprovalLevel.objects.filter(
                level=level,
                asset_type=asset_type,
                branch=branch
            ).exists():
                raise serializers.ValidationError(
                    f"An approval level with level={level} already exists for branch '{branch}' and request type '{asset_type}'."
                )

        return attrs
    def to_representation(self, instance):
        rep = super(AssetApprovalLevelSerializer, self).to_representation(instance)
        if instance.asset_type:  
            rep['asset_type'] = instance.asset_type.name
        if instance.approver:  
            rep['approver'] = instance.approver.username
        if instance.branch.exists():  
            rep['branch'] = [cat.branch_name for cat in instance.branch.all()]
        return rep
    
    
class AssetApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetApproval
        fields = '__all__'

    def to_representation(self, instance):
        rep = super(AssetApprovalSerializer, self).to_representation(instance)
        if instance.asset_request:  
            rep['asset_request'] = str(instance.asset_request.asset_type)
        if instance.approver:  
            rep['approver'] = instance.approver.username       
        return rep       

class AssetAllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetAllocation
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(AssetAllocationSerializer, self).to_representation(instance)
        if instance.asset:
            rep['asset'] =instance.asset.name
        if instance.employee:
            rep['employee'] =instance.employee.emp_code
        return rep
    
class AssetEmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetEmailTemplate
        fields = '__all__'
    def validate(self, attrs):
        template_type=attrs.get("template_type")
        temp=AssetEmailTemplate.objects.filter(template_type=template_type)
        if self.instance:
            temp=temp.exclude(id=self.instance.id)
        if temp.exists():
            raise serializers.ValidationError({"template_name": f"{template_type} template already exists."
        })
        return attrs



class AssetRequestSerializer(serializers.ModelSerializer):
    approvals = AssetApprovalSerializer(many=True, read_only=True)

    class Meta:
        model = AssetRequest
        fields = '__all__'
    def validate(self, attrs):
        asset_type = attrs.get('asset_type')
        requested_asset = attrs.get('requested_asset')

        # 1. Asset Type must be entered
        if not asset_type:
            raise serializers.ValidationError({
                "asset_type": "Asset Type is required."
            })

        # 2. Asset must be selected
        if not requested_asset:
            raise serializers.ValidationError({
                "requested_asset": "Requested asset is required."
            })

        # 3. Asset must belong to entered asset type
        if requested_asset.asset_type != asset_type:
            raise serializers.ValidationError({
                "requested_asset": "Selected asset does not belong to the chosen asset type."
            })

        # 4. Asset must be AVAILABLE
        if requested_asset.status != 'available':
            raise serializers.ValidationError({
                "requested_asset": f"This asset is not available (current status: {requested_asset.status})."
            })

        return attrs
    def to_representation(self, instance):
        rep = super(AssetRequestSerializer, self).to_representation(instance)

        # Show names in response, but IDs remain for POST
        if instance.asset_type:
            rep['asset_type'] = instance.asset_type.name

        if instance.employee:
            rep['employee_code'] = instance.employee.emp_code

        if instance.requested_asset:
            rep['requested_asset'] = instance.requested_asset.name

        return rep
    
class AssetReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetReport
        fields = '__all__' 
class AssetTransactionReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetTransactionReport
        fields = '__all__'

class GratuityTableSerializer(serializers.ModelSerializer):
    class Meta:
        model = GratuityTable
        fields = '__all__'

class DocumentSerializer(serializers.ModelSerializer):
    # uploaded_by_name = serializers.CharField(source='uploaded_by.username', read_only=True)

    class Meta:
        model = Document
        fields = ['id', 'name', 'file', 'folder', 'uploaded_at']


class FolderSerializer(serializers.ModelSerializer):
    subfolders = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()
    path = serializers.ReadOnlyField()

    class Meta:
        model = Folder
        fields = [
            'id',
            'name',
            'path',
            'parent',
            'subfolders',
            'documents',
            'created_by',
            'created_at'
        ]

    def get_subfolders(self, obj):
        subfolders = obj.subfolders.all().order_by('name')
        return FolderSerializer(subfolders, many=True, context=self.context).data

    def get_documents(self, obj):
        docs = obj.documents.all().order_by('-uploaded_at')
        return DocumentSerializer(docs, many=True, context=self.context).data

class BranchSerializer(serializers.ModelSerializer):
    from OrganisationManager.serializer import AnnouncementSerializer
    holidays = serializers.SerializerMethodField()
    policies = serializers.SerializerMethodField()  # Add this field
    branch_announcements = AnnouncementSerializer(many=True, read_only=True)
    branch_projects = ProjectSerializer(many=True, read_only=True)

    class Meta:
        model = brnch_mstr
        fields = '__all__'

    def get_holidays(self, obj):
        from calendars.serializer import HolidaySerializer  # Ensure correct import path
        # Fetch assigned holiday calendars for this branch
        assigned_holiday_calendars = assign_holiday.objects.filter(branch__in=[obj]).values_list('holiday_model', flat=True)
        holidays = holiday.objects.filter(calendar__in=assigned_holiday_calendars)
        return HolidaySerializer(holidays, many=True).data
    
    def get_policies(self, obj):
        """Fetch company policies assigned to this branch."""
        # from OrganisationManager.serializer import CompanyPolicySerializer  # Import the serializer
        policies = obj.policies.all()  # Using related_name='policies' from CompanyPolicy model
        return CompanyPolicySerializer(policies, many=True, context={'request': self.context.get('request')}).data
    
    def get_branch_announcements(self, obj):
        announcements = obj.branch_announcements.filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
        )
        return AnnouncementSerializer(announcements, many=True).data