from django.db import models
from OrganisationManager.models import brnch_mstr
# Create your models here.
from django.contrib.auth.models import AbstractUser,AbstractBaseUser,BaseUserManager,PermissionsMixin
from phonenumber_field.modelfields import PhoneNumberField
from .manager import CustomUserManager
from django.contrib.auth import get_user_model
import uuid
from tenant_users.tenants.models import TenantBase,UserProfile
# from tenant_users.tenants.tasks import provision_tenant
from django_tenants.models import TenantMixin, DomainMixin
from tenant_users.tenants.models import UserTenantPermissions
import re
from django.utils.text import slugify
from django.core.validators import RegexValidator

from django_tenants.utils import schema_context

class company(TenantMixin):
    name = models.CharField(max_length=100)
    schema_name = models.SlugField(unique=True, blank=True, null=True)  # Ensure unique schemas
    paid_until = models.DateField(auto_now_add=True)
    created_on = models.DateField(auto_now_add=True)
    country = models.ForeignKey('Core.cntry_mstr', on_delete=models.CASCADE)
    logo = models.ImageField(null=True, blank=True)
    employer_unique_id = models.CharField(
        max_length=13,
        validators=[RegexValidator(r'^\d{13}$', 'Must be a 13-digit number')],
        help_text="13-digit Employer Unique ID from Ministry of Labor",
        blank=True,
        null=True
    )
    bank_routing_code = models.CharField(
        max_length=9,
        validators=[RegexValidator(r'^\d{9}$', 'Must be a 9-digit number')],
        help_text="9-digit bank routing code for employer's corporate account",
        blank=True,
        null=True
    )
    # Automatically create schema when saving
    auto_create_schema = True
    @property
    def currency(self):
        return getattr(self.country, 'currency', None)
    def save(self, *args, **kwargs):
        # Generate schema_name from name if not explicitly provided
        if not self.schema_name:
            # Remove special characters and convert to lowercase
            cleaned_name = re.sub(r'[^a-zA-Z0-9]', '', self.name)
            # Ensure schema_name is unique by slugifying and appending a number if needed
            base_schema_name = slugify(cleaned_name)
            self.schema_name = base_schema_name

            # Check for uniqueness
            existing_schemas = type(self).objects.filter(schema_name__startswith=base_schema_name)
            if existing_schemas.exists():
                count = existing_schemas.count()
                self.schema_name = f"{base_schema_name}{count + 1}"

        super().save(*args, **kwargs)  # Save the company and create the schema

        # Check if Domain exists (optional, using a Manager)
        if not Domain.objects.filter(domain=f"{self.schema_name}.localhost").exists():
            Domain.objects.create(domain=f"{self.schema_name}.localhost", tenant=self)

        # Switch to the newly created schema using schema_context
        with schema_context(self.schema_name):
            # Create the branch within the company's schema
            brnch_mstr.objects.create(
                branch_name=self.name,
                branch_logo=self.logo,
                branch_code='BR001',  # Provide a default or unique branch code
                probation_period_days = 30,  # Provide a value for non-nullable field
                br_country_id=1,  # Set the country or use default ID
                br_city='Sample City',
                br_pincode='123456',
                br_branch_nmbr_1='BR-0001',  # Provide a unique branch number
                br_branch_mail='branch@example.com',  # Provide a valid email
            )
    def __str__(self):
        return self.name

    
class Domain(DomainMixin):
    pass


class CustomUser(UserProfile):
    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(
        ("Email Address"),
        unique=True,
        blank=True,  # Allow email to be optional
        null=True,   # Allow email to be optional
    )
    is_ess = models.BooleanField(default=False, null=True, blank=True)
    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)
    is_active=models.BooleanField(default=True)

    USERNAME_FIELD = 'username'  # Authentication uses username
    REQUIRED_FIELDS = ['email']  # No required fields other than username

    objects = CustomUserManager()

    def save(self, *args, **kwargs):
        if not self.pk:  # Only set is_active=True on creation
            self.is_active = True
        super().save(*args, **kwargs)
    def __str__(self):
        return self.username
    # Add any additional fields if needed

