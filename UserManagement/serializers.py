
from rest_framework import serializers
from .models import CustomUser,company,Domain
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.contrib.contenttypes.models import ContentType
from django_tenants.utils import schema_context
# from Core .models import TaxSystem,crncy_mstr
from Core .models import TaxSystem,crncy_mstr,state_mstr
from Core .serializer import StateSerializer
from tenant_users.tenants.models import UserTenantPermissions
       
class CustomUserSerializer(serializers.ModelSerializer):
    tenants = serializers.PrimaryKeyRelatedField(queryset=company.objects.all(), many=True, write_only=True)
    allocated_tenants = serializers.SerializerMethodField(read_only=True)
    user_groups = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = CustomUser
        fields = '__all__'
        extra_kwargs = {
            'password': {'write_only': True, 'required': False, 'allow_null': True, 'default': None}
        }
    def get_allocated_tenants(self, obj):
        tenants = obj.tenants.all()
        serializer = UserAllocatedCompanySerializer(
            tenants,
            many=True,
            context={"user": obj}
        )
        return serializer.data
    def get_user_groups(self, obj):
        """Retrieve assigned groups for the user via UserTenantPermissions."""
        try:
            user_tenant_permissions = UserTenantPermissions.objects.get(profile=obj)
            return [group.name for group in user_tenant_permissions.groups.all()]  # Fetch group names
        except UserTenantPermissions.DoesNotExist:
            return []  # Return empty if no groups assigned
    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['allocated_tenants'] = self.get_allocated_tenants(instance)
        rep['user_groups'] = self.get_user_groups(instance)  # Include group names
        return rep

    def create(self, validated_data):
        tenants_data = validated_data.pop('tenants', [])
        password = validated_data.pop('password', None)
        user = super().create(validated_data)
        if password:  # Only set password if provided
            user.set_password(password)
        user.save()
        
        # Add tenants to user
        user.tenants.set(tenants_data)
        return user

    def update(self, instance, validated_data):
        # Pop optional fields
        tenants_data = validated_data.pop('tenants', None)
        password = validated_data.pop('password', None)

        # Update password only if provided and not None
        if password is not None:
            instance.set_password(password)

        # Update other fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        instance.save()

        # Update tenants if provided
        if tenants_data is not None:
            instance.tenants.set(tenants_data)
        
        return instance

# class CustomUserSerializer(UserSerializer):
# from oauth2_provider.models import AccessToken
from django.utils import timezone
import uuid
from datetime import timedelta




class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        # Determine if the user is attempting to authenticate with username or email
        username_or_email = attrs.get("username")
        password = attrs.get("password")

        # Check if username_or_email is a valid email format
        if "@" in username_or_email:
            user = CustomUser.objects.filter(email=username_or_email).first()
        else:
            user = CustomUser.objects.filter(username=username_or_email).first()

        if user:
            # Check if the user is active, if not, prevent login
            if not user.is_active:
                raise serializers.ValidationError("Your account is deactivated. Please contact support.")

            # Validate the user based on the is_ess field
            if (user.is_ess and user.username == username_or_email) or (
                    not user.is_ess and user.email == username_or_email):
                if user.check_password(password):
                    self.user = user
                    data = super().validate(attrs)
                    data["user_id"] = user.id
                    data["username"] = user.username
                    data["tenants"] = [
                        {
                            "id": tenant.id,
                            "name": tenant.name,
                            "schema_name": tenant.schema_name
                        }
                        for tenant in user.tenants.all()
                    ]
                    data["tenant_id"] = [tenant.schema_name for tenant in user.tenants.all()]
                    return data
                else:
                    raise serializers.ValidationError("Invalid password")
            else:
                if user.is_ess:
                    raise serializers.ValidationError("ESS users must authenticate with their username")
                else:
                    raise serializers.ValidationError("Non-ESS users must authenticate with their email")
        else:
            raise serializers.ValidationError("User not found")

        return super().validate(attrs)
    
class CompanySerializer(serializers.ModelSerializer):
    branches = serializers.SerializerMethodField()
    tax_details = serializers.SerializerMethodField()
    currency_details = serializers.SerializerMethodField()
    state_label = serializers.SerializerMethodField()  # Avoid duplicate logic
    states = serializers.SerializerMethodField()
    # country = serializers.CharField(source='country.country_name', read_only=True)
    # state = serializers.CharField(source='state.state_name', read_only=True)
    # currency = serializers.CharField(source='currency.currency_name', read_only=True)

    class Meta:
        model = company
        fields = '__all__'
    def get_branches(self, obj):
        from OrganisationManager.models import brnch_mstr
        from django_tenants.utils import schema_context

        """
        Return branch id and name for this company (tenant)
        """
        try:
            with schema_context(obj.schema_name):
                return list(
                    brnch_mstr.objects.all()
                    # .filter(br_is_active=True)
                    .values(
                        "id",
                        "branch_name"
                    )
                )
        except Exception:
            return []
    def get_states(self, obj):
        if obj.country:
            states = state_mstr.objects.filter(country=obj.country, is_active=True)
            return StateSerializer(states, many=True).data
        return []
    def get_tax_details(self, obj):
        """Fetch tax details dynamically from the TaxSystem model"""
        tax = TaxSystem.objects.filter(country=obj.country, is_active=True).first()
        if tax:
            return {"tax_name": tax.tax_name, "tax_percentage": tax.tax_percentage}
        return None  # If no tax is found  
    def get_currency_details(self, obj):
        currency = obj.currency
        if not currency and obj.country:
            currency = obj.country.currency.first()

        if currency:
            return {
                "currency_name": currency.currency_name,
                "currency_code": currency.currency_code,
                "symbol": currency.symbol
            }

        return None
    def get_state_label(self, obj):
        return obj.country.get_state_label() if obj.country else None  # Use method from model
class Non_EssUserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'email', 'is_ess', 'is_staff', 'is_superuser', 'tenants']

class UserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'email', 'is_ess', 'is_staff', 'is_superuser', 'tenants']

class DomainSerializer(serializers.ModelSerializer):
    class Meta:
        model=Domain
        fields = '__all__'


class ValidateCredentialsSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()

    def validate(self, attrs):
        username_or_email = attrs.get("username")
        password = attrs.get("password")

        # Check username or email
        if "@" in username_or_email:
            user = CustomUser.objects.filter(email=username_or_email).first()
        else:
            user = CustomUser.objects.filter(username=username_or_email).first()

        if user is None:
            raise serializers.ValidationError("User not found")

        if not user.is_active:
            raise serializers.ValidationError("User is deactivated")

        if not user.check_password(password):
            raise serializers.ValidationError("Invalid password")

        # SUCCESS → credentials are valid
        return {"user_id": user.id}
class UserAllocatedCompanySerializer(CompanySerializer):
    branches = serializers.SerializerMethodField()

    class Meta(CompanySerializer.Meta):
        fields = "__all__"

    def get_branches(self, obj):
        from OrganisationManager.models import UserBranchAccess, brnch_mstr
        from django_tenants.utils import schema_context

        user = self.context.get("user")
        if not user:
            return []

        try:
            with schema_context(obj.schema_name):

                # 🔥 SUPERUSER → ALL BRANCHES
                if user.is_superuser:
                    return list(
                        brnch_mstr.objects.all().values(
                            "id",
                            "branch_name"
                        )
                    )

                # 👤 NORMAL USER → ASSIGNED BRANCHES
                qs = (
                    UserBranchAccess.objects
                    .filter(user=user)
                    .prefetch_related("branch")
                )

                branches = []
                for access in qs:
                    for b in access.branch.all():
                        branches.append({
                            "id": b.id,
                            "name": b.branch_name
                        })

                return branches

        except Exception as e:
            print(e)
            return []