from .models import (brnch_mstr,dept_master,desgntn_master,DocumentNumbering,
                     ctgry_master,FiscalPeriod,FiscalYear,CompanyPolicy,
                     Announcement,AnnouncementView,AnnouncementComment,Asset,AssetAllocation,AssetType, AssetRequest,AssetCustomField,AssetReport,
                     AssetCustomFieldValue,AssetTransactionReport,GratuityTable)
from rest_framework import serializers
from tenant_users.tenants.models import UserTenantPermissions
from django.contrib.auth.models import Permission,Group
from calendars .models import assign_holiday,holiday,holiday_calendar
from django.utils import timezone

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
class BranchSerializer(serializers.ModelSerializer):
    holidays = serializers.SerializerMethodField()
    policies = serializers.SerializerMethodField()  # Add this field

    class Meta:
        model = brnch_mstr
        fields = '__all__'

    # def get_holidays(self, obj):
    #     from calendars.serializer import HolidaySerializer
    #     assigned_holidays = assign_holiday.objects.filter(branch=obj).values_list('holiday_model__holiday', flat=True)
    #     holidays = holiday.objects.filter(id__in=assigned_holidays)
    #     return HolidaySerializer(holidays, many=True).data
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
    
    def get_announcements(self, obj):
        from OrganisationManager.serializer import AnnouncementSerializer  
        announcements = obj.branch_announcements.all()  #Use the related_name from Announcement model
        # Optional: filter out expired announcements
        current_time = timezone.now()
        active_announcements = announcements.filter(expires_at__isnull=True) | announcements.filter(expires_at__gt=current_time)
        return AnnouncementSerializer(active_announcements.distinct(), many=True, context={'request': self.context.get('request')}).data

    
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
class AssetAllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetAllocation
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(AssetAllocationSerializer, self).to_representation(instance)
        if instance.asset:
            rep['asset'] =instance.asset.asset_type.name
        if instance.employee:
            rep['employee'] =instance.employee.emp_code
        return rep
class AssetRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetRequest
        fields = '__all__'
    def to_representation(self, instance):
        rep = super(AssetRequestSerializer, self).to_representation(instance)
        if instance.asset_type:
            rep['asset_type'] =instance.asset_type.name
        if instance.employee:
            rep['employee'] =instance.employee.emp_code
        if instance.requested_asset:
            rep['requested_asset'] =instance.requested_asset.name
        return rep


   

class AssetTypeSerializer(serializers.ModelSerializer):
    asset_custom_fields=AssetCustomFieldValueSerializer(many=True, read_only=True, source='field_values')
    class Meta:
        model = AssetType
        fields = '__all__'
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