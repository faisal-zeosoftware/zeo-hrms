from django.db import models
# from UserManagement.models import company
from phonenumber_field.modelfields import PhoneNumberField
from django.contrib.auth import get_user_model
from datetime import timedelta,timezone
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db.models.signals import pre_save
from datetime import datetime,date
import re
from django.utils import timezone
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import connection
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.core.mail import EmailMessage
from email.utils import formataddr
from django.conf import settings
from django.contrib.postgres.fields import JSONField
from django.contrib.sites.models import Site
from django.template import Context, Template
from django.utils import timezone
from django.core.mail import EmailMultiAlternatives,get_connection, send_mail
from Core .models import LanguageSkill,MarketingSkill,ProgrammingLanguageSkill
from django.core.validators import RegexValidator
import logging
logger = logging.getLogger((__name__))
from .utils import send_notification_email,get_employee_context,schedule_escalation
from django.db import transaction



#EmpManagement
class emp_master(models.Model):    
    GENDER_CHOICES = [ ("M", "Male"), ("F", "Female"),("O", "Other"),]
    MARITAL_STATUS_CHOICES = [("M", "Married"),("S", "Single"),('divorced','divorced'),('widow','widow')]
    
    emp_code                 = models.CharField(max_length=50,unique=True)
    emp_first_name           = models.CharField(max_length=50,null=True,blank =True)
    emp_middle_name          = models.CharField(max_length=50,null=True,blank =True)
    emp_last_name            = models.CharField(max_length=50,null=True,blank =True)
    emp_gender               = models.CharField(max_length=20,choices=GENDER_CHOICES,null=True,blank =True)
    emp_date_of_birth        = models.DateField(null=True,blank =True)
    emp_personal_email       = models.EmailField(null=True,blank =True)
    emp_company_email        = models.EmailField(null=True,blank =True)
    emp_mobile_number_1      = models.CharField(null=True,blank =True)
    emp_mobile_number_2      = models.CharField(null=True,blank =True)
    emp_reporting_manager    = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True,  related_name='emp_reporting_manager')
    emp_country_id           = models.ForeignKey("Core.cntry_mstr",on_delete=models.SET_NULL,null=True,blank =True)
    emp_state_id             = models.ForeignKey("Core.state_mstr",on_delete=models.SET_NULL,null=True,blank =True)
    emp_city                 = models.CharField(max_length=50,null=True,blank =True)
    emp_permenent_address    = models.CharField(max_length=200,null=True,blank =True)
    emp_present_address      = models.CharField(max_length=200,blank=True,null=True)
    emp_status               = models.BooleanField(default=True,null=True,blank =True)
    emp_joined_date          = models.DateField()
    emp_date_of_confirmation = models.DateField(null=True,blank =True)
    emp_relegion             = models.ForeignKey("Core.ReligionMaster",on_delete=models.SET_NULL,null=True,blank =True)
    emp_profile_pic          = models.ImageField(upload_to="emp_profile_pic/",null=True,blank =True )
    emp_blood_group          = models.CharField(max_length=50,blank=True,null=True)
    emp_nationality          = models.ForeignKey("Core.Nationality",on_delete=models.SET_NULL,null=True,blank =True)
    emp_marital_status       = models.CharField(max_length=10,choices=MARITAL_STATUS_CHOICES,null=True,blank =True)
    emp_father_name          = models.CharField(max_length=50,null=True,blank =True)
    emp_mother_name          = models.CharField(max_length=50,null=True,blank =True)
    created_at               = models.DateTimeField(auto_now_add=True,null=True,blank =True)
    created_by               = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True,  related_name='emp_created_by1')
    updated_at               = models.DateTimeField(auto_now=True,null=True,blank =True)
    updated_by               = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='emp_updated_by1')
    is_active                = models.BooleanField(default=True,null=True,blank =True)
    emp_ot_applicable        = models.BooleanField(default=False,null=True,blank =True)
    is_ess                   = models.BooleanField(default=False,blank =True)
    emp_branch_id            = models.ForeignKey("OrganisationManager.brnch_mstr",on_delete=models.SET_NULL,null=True,blank =True)
    emp_dept_id              = models.ForeignKey("OrganisationManager.dept_master",on_delete=models.SET_NULL,null=True,blank =True)
    emp_desgntn_id           = models.ForeignKey("OrganisationManager.desgntn_master",on_delete=models.SET_NULL,null=True,blank =True)
    emp_ctgry_id             = models.ForeignKey("OrganisationManager.ctgry_master",on_delete=models.SET_NULL,null=True,blank =True)
    emp_weekend_calendar     = models.ForeignKey("calendars.weekend_calendar",on_delete=models.SET_NULL,null=True,blank =True)
    holiday_calendar         = models.ForeignKey("calendars.holiday_calendar",on_delete=models.SET_NULL,null=True,blank =True)
    users                    = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, related_name='employees',null=True,blank =True)
    person_id                = models.CharField(max_length=14,unique=True,validators=[RegexValidator(r'^\d{14}$', 'Must be a 14-digit number')],help_text="14-digit Person ID from Ministry of Labor",blank=True,null=True)    
    work_location            = models.ForeignKey('OrganisationManager.brnch_mstr',on_delete=models.SET_NULL,related_name='work_location',null=True,blank =True)
    visa_location            = models.ForeignKey('OrganisationManager.brnch_mstr', on_delete=models.SET_NULL,related_name='visa_location',null=True,blank =True)
    face_encoding            = models.JSONField(null=True, blank=True)
    barcode_number           = models.CharField(max_length=100, unique=True, null=True, blank=True, help_text="Unique barcode or card ID for attendance scanning")
    ATTENDANCE_SOURCE_CHOICES = [
        ("manual", "Manual Attendance"),
        ("biometric", "Biometric Attendance"),]
    attendance_source = models.CharField(max_length=20,choices=ATTENDANCE_SOURCE_CHOICES,default="manual",help_text="Primary attendance source for payroll calculations",blank=True,null=True)
    class Meta:
        permissions = (
            ('import_emp_master', 'Can import employee master'),
            
            # Add more custom permissions here
        )
    def save(self, *args, **kwargs):
        created = not self.pk
        authenticated_user = kwargs.pop('authenticated_user', None)

        # Set probation period
        if self.emp_joined_date and self.emp_branch_id:
            self.emp_date_of_confirmation = self.emp_joined_date + timedelta(
                days=self.emp_branch_id.probation_period_days
            )
        if self.person_id == '':
            self.person_id = None
        # Set created_by and is_active for new records
        if created:
            if authenticated_user:
                self.created_by = authenticated_user
            self.is_active = True

        # ---- Check if is_ess changed ----
        create_or_reactivate_user_required = False
        deactivate_user_required = False

        if not created:
            old_instance = emp_master.objects.filter(pk=self.pk).first()
            if old_instance:
                # Case 1: was False, now True → create/reactivate user
                if not old_instance.is_ess and self.is_ess:
                    create_or_reactivate_user_required = True

                # Case 2: was True, now False → deactivate user
                if old_instance.is_ess and not self.is_ess:
                    deactivate_user_required = True

        super().save(*args, **kwargs)

        # Case 1: New employee created with is_ess=True
        if created and self.is_ess:
            create_or_reactivate_user_required = True

        # --- Handle User Creation/Activation ---
        if create_or_reactivate_user_required:
            user_model = get_user_model()
            username = self.emp_code
            email = self.emp_personal_email
            password = 'admin'  # ⚠️ Consider generating secure password
            schema_name = connection.schema_name

            try:
                from UserManagement.models import company
                company_instance = company.objects.get(schema_name=schema_name)
            except company.DoesNotExist:
                company_instance = None
                logger.error(f"No company found for schema: {schema_name}")

            try:
                # Check if a user already exists for this employee code
                user = user_model.objects.filter(username=username).first()

                if user:
                    # Reactivate instead of creating duplicate
                    user.is_active = True
                    user.is_ess = True
                    user.email = email or user.email  # update email if available
                    user.save()
                    self.users = user
                    super().save(update_fields=['users'])
                    logger.info(f"User {user.username} reactivated and linked to employee {self.emp_code}.")
                else:
                    # Create new user
                    user = user_model.objects.create_user(
                        username=username,
                        email=email,
                        password=password,
                        is_ess=True,
                        must_change_password=True
                    )
                    self.users = user
                    super().save(update_fields=['users'])

                    if company_instance:
                        user.tenants.set([company_instance])

                    user.is_active = True
                    user.is_ess = True
                    user.must_change_password = True
                    user.save()
                    logger.info(f"User {user.username} created and linked to employee {self.emp_code}.")
            except Exception as e:
                logger.error(f"Error handling user for {self.emp_code}: {e}")
                raise

        # --- Handle User Deactivation ---
        if deactivate_user_required:
            if self.users:
                self.users.is_active = False
                self.users.is_ess = False
                self.users.save()
                logger.info(f"User {self.users.username} deactivated because ESS was disabled.")    
    def delete(self, *args, **kwargs):
        """
        Instead of deleting, mark the employee as inactive.
        Also, mark the associated user as inactive if it exists.
        """
        self.__class__.objects.filter(pk=self.pk).update(is_active=False)  # Mark employee inactive

        # Deactivate the user with the same emp_code as username
        user_model = get_user_model()
        try:
            user_model.objects.filter(username=self.emp_code).update(is_active=False)  # Use update() instead of save()
            logger.info(f"User {self.emp_code} deactivated successfully.")
        except Exception as e:
            logger.warning(f"Error deactivating user {self.emp_code}: {e}")
    
    def __str__(self):
        return self.emp_code
    
    def get_custom_fields(self):
        return self.custom_fields.all()
    
    def get_attendance(self):
        from calendars .models import Attendance
        # Fetch approvals assigned to this user
        return Attendance.objects.filter(employee=self)
    def get_leave_balance(self):
        from calendars.models import emp_leave_balance
        return emp_leave_balance.objects.filter(employee=self)
class Report(models.Model):
    file_name   = models.CharField(max_length=100,unique=True)
    report_data = models.FileField(upload_to='employee_report/', null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    created_by  = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
 
    # created_at = models.DateTimeField(auto_now_add=True,null=True,blank =True)
    class Meta:
        permissions = (
            ('emp_export_report', 'Can export employee report'),
            
            # Add more custom permissions here
        )
    
    
    def __str__(self):
        return self.file_name

class Doc_Report(models.Model):
    file_name   = models.CharField(max_length=100,unique=True)
    report_data = models.FileField(upload_to='document_report/', null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    created_by  = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    class Meta:
        permissions = (
            ('export_document_report', 'Can export doc report'),
            # Add more custom permissions here
        )

    def __str__(self):
        return self.file_name

class GeneralRequestReport(models.Model):
    file_name   = models.CharField(max_length=100,unique=True)
    report_data = models.FileField(upload_to='general_report/', null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    created_by  = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    class Meta:
        permissions = (
            ('export_general_request_report', 'Can export general request report'),
            # Add more custom permissions here
        )
    
    def __str__(self):
        return self.file_name

class Emp_CustomField(models.Model):
    FIELD_TYPES = (   
        ('dropdown', 'DropdownField'),
        ('radio', 'RadioButtonField'),
        ('date', 'DateField'),
        ('text', 'TextField'),
        ('checkbox', 'CheckboxField'),
    )
    # emp_master = models.ForeignKey(emp_master, on_delete=models.CASCADE, related_name='custom_fields',null=True)
    emp_custom_field = models.CharField(unique=True,max_length=100)  # Field name provided by end user
    data_type        = models.CharField(max_length=20, choices=FIELD_TYPES, null=True, blank=True)
    dropdown_values  = models.JSONField(null=True, blank=True)
    radio_values     = models.JSONField(null=True, blank=True)
    checkbox_values  = models.JSONField(null=True,blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    created_by       = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    def __str__(self):
        return self.emp_custom_field    
    
    def clean(self):
        # Validate dropdown field values
        if self.data_type == 'dropdown':
            if self.dropdown_values:
                options = self.dropdown_values
                if  not  options:
                    raise ValidationError({'field_value': 'Select a value from the dropdown options.'})
            else:
                raise ValidationError({'field_value': 'provide value to the dropdown options.'})
        # Validate radio field values
        elif self.data_type == 'radio':
            if self.radio_values:
                options = self.radio_values
                if not  options:
                    raise ValidationError({'field_value': 'Select a value from the radio options.'})
            else:
                raise ValidationError({'field_value': 'provide value to the radio options.'})
        # Validate checkbox field values
        elif self.data_type == 'checkbox':
            if self.checkbox_values:
                options = self.checkbox_values
                if not  options:
                    raise ValidationError({'field_value': 'Select a value from the checkbox options.'})
            else:
                raise ValidationError({'field_value': 'provide value to the checkbox options.'})
    def save(self, *args, **kwargs):
        self.clean()  # Call clean to perform validation
        super().save(*args, **kwargs)
    

class Emp_CustomFieldValue(models.Model):
    emp_custom_field = models.CharField(max_length=100)
    field_value      = models.TextField(null=True, blank=True)  # Field value provided by end user
    emp_master       = models.ForeignKey('emp_master', on_delete=models.CASCADE, related_name='custom_field_values')
    created_at       = models.DateTimeField(auto_now_add=True)
    created_by       = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True,blank=True, related_name='%(class)s_created_by')
 

    def __str__(self):
        return f'{self.emp_custom_field.emp_custom_field}: {self.field_value}'

    def save(self, *args, **kwargs):
        if not self.emp_custom_field:
            raise ValueError("Field name cannot be None or empty.")
        if not Emp_CustomField.objects.filter(emp_custom_field=self.emp_custom_field).exists():
            raise ValueError(f"Field name '{self.emp_custom_field}' does not exist in Emp_CustomField.")

        # Check if a custom field value already exists for the same emp_master and emp_custom_field
        existing_value = Emp_CustomFieldValue.objects.filter(
            emp_custom_field=self.emp_custom_field,
            emp_master=self.emp_master
        ).first()

        if existing_value:
            # If it exists, update the existing record instead of creating a new one
            existing_value.field_value = self.field_value
            # Use update() to avoid calling save() and prevent recursion
            Emp_CustomFieldValue.objects.filter(
                id=existing_value.id
            ).update(field_value=self.field_value)
        else:
            # Call full_clean to ensure that the clean method is called
            self.full_clean()
            super().save(*args, **kwargs)

    def clean(self):
        # Retrieve the custom field object
        custom_field = Emp_CustomField.objects.filter(emp_custom_field=self.emp_custom_field).first()

        if not custom_field:
            raise ValidationError(f"Field name '{self.emp_custom_field}' does not exist in Emp_CustomField.")
        
        field_value = self.field_value

        if custom_field.data_type == 'dropdown':
            if custom_field.dropdown_values:
                options = custom_field.dropdown_values
                if not field_value or field_value not in options:
                    raise ValidationError({'field_value': 'Select a value from the dropdown options.'})
        
        elif custom_field.data_type == 'radio':
            if custom_field.radio_values:
                options = custom_field.radio_values
                if not field_value or field_value not in options:
                    raise ValidationError({'field_value': 'Select a value from the radio options.'})
       
        elif custom_field.data_type == 'checkbox':
            if custom_field.checkbox_values:
                options = custom_field.checkbox_values
                if not field_value or field_value not in options:
                    raise ValidationError({'field_value': 'Select a value from the checkbox options.'})


        elif custom_field.data_type == 'date':
            if not field_value:
                raise ValidationError({'field_value': 'Date value is required.'})

            try:
                if isinstance(field_value, (datetime, date)):
                    valid_date = field_value.strftime('%d-%m-%Y')
                else:
                    field_value_str = str(field_value).strip()
                    # Remove time part if exists
                    if ' ' in field_value_str:
                        field_value_str = field_value_str.split(' ')[0]
                    # Try multiple formats
                    for fmt in ('%d-%m-%Y', '%d/%m/%Y', '%Y-%m-%d'):
                        try:
                            valid_date = datetime.strptime(field_value_str, fmt).strftime('%d-%m-%Y')
                            break
                        except ValueError:
                            continue
                    else:
                        raise ValueError
                self.field_value = valid_date
            except ValueError:
                raise ValidationError({'field_value': 'Invalid date format. Allowed formats: DD-MM-YYYY or DD/MM/YYYY.'})



#EMPLOYEE FAMILY(ef) data
class emp_family(models.Model):
    emp_id             = models.ForeignKey('emp_master',on_delete = models.CASCADE,related_name='emp_family')
    ef_member_name     = models.CharField(max_length=50)
    emp_relation       = models.CharField(max_length=50)
    ef_company_expence = models.FloatField()
    ef_date_of_birth   = models.DateField()
    created_at         = models.DateTimeField(auto_now_add=True)
    created_by         = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    updated_at         = models.DateTimeField(auto_now=True)
    updated_by         = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_updated_by')
    is_active          = models.BooleanField(default=True)
    
    
    def __str__(self):
        return self.ef_member_name

#EMPLOYEE FAMILY UDF
class EmpFamily_CustomField(models.Model):
    FIELD_TYPES = (   
        ('dropdown', 'DropdownField'),
        ('radio', 'RadioButtonField'),
        ('date', 'DateField'),
        ('text', 'TextField'),
        ('checkbox', 'CheckboxField'),
    )
    # emp_master = models.ForeignKey(emp_master, on_delete=models.CASCADE, related_name='custom_fields',null=True)
    emp_custom_field = models.CharField(unique=True,max_length=100,null=True)  # Field name provided by end user
    data_type        = models.CharField(max_length=20, choices=FIELD_TYPES, null=True, blank=True)
    dropdown_values  = models.JSONField(null=True, blank=True)
    radio_values     = models.JSONField(null=True, blank=True)
    checkbox_values  = models.JSONField(null=True,blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    created_by       = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    def __str__(self):
        return self.emp_custom_field    
    
    def clean(self):
        # Validate dropdown field values
        if self.data_type == 'dropdown':
            if self.dropdown_values:
                options = self.dropdown_values
                if  not  options:
                    raise ValidationError({'field_value': 'Select a value from the dropdown options.'})
            else:
                raise ValidationError({'field_value': 'provide value to the dropdown options.'})
        # Validate radio field values
        elif self.data_type == 'radio':
            if self.radio_values:
                options = self.radio_values
                if not  options:
                    raise ValidationError({'field_value': 'Select a value from the radio options.'})
            else:
                raise ValidationError({'field_value': 'provide value to the radio options.'})
        # Validate checkbox field values
        elif self.data_type == 'checkbox':
            if self.checkbox_values:
                options = self.checkbox_values
                if not  options:
                    raise ValidationError({'field_value': 'Select a value from the checkbox options.'})
            else:
                raise ValidationError({'field_value': 'provide value to the checkbox options.'})
    def save(self, *args, **kwargs):
        self.clean()  # Call clean to perform validation
        super().save(*args, **kwargs)
    
class Fam_CustomFieldValue(models.Model):
    emp_custom_field = models.CharField(max_length=100)
    field_value      = models.TextField(null=True, blank=True)  # Field value provided by end user
    emp_family       = models.ForeignKey('emp_family', on_delete=models.CASCADE, related_name='custom_field_values')
    created_at       = models.DateTimeField(auto_now_add=True)
    # created_by       = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
 

    def __str__(self):
        return f'{self.emp_custom_field.emp_custom_field}: {self.field_value}'

    def save(self, *args, **kwargs):
        if not self.emp_custom_field:
            raise ValueError("Field name cannot be None or empty.")
        if not EmpFamily_CustomField.objects.filter(emp_custom_field=self.emp_custom_field).exists():
            raise ValueError(f"Field name '{self.emp_custom_field}' does not exist in Emp_CustomField.")

        # Check if a custom field value already exists for the same emp_master and emp_custom_field
        existing_value = Fam_CustomFieldValue.objects.filter(
            emp_custom_field=self.emp_custom_field,
            emp_family=self.emp_family
        ).first()

        if existing_value:
            # If it exists, update the existing record instead of creating a new one
            existing_value.field_value = self.field_value
            # Use update() to avoid calling save() and prevent recursion
            Fam_CustomFieldValue.objects.filter(
                id=existing_value.id
            ).update(field_value=self.field_value)
        else:
            # Call full_clean to ensure that the clean method is called
            self.full_clean()
            super().save(*args, **kwargs)

    def clean(self):
        # Retrieve the custom field object
        custom_field = EmpFamily_CustomField.objects.filter(emp_custom_field=self.emp_custom_field).first()

        if not custom_field:
            raise ValidationError(f"Field name '{self.emp_custom_field}' does not exist in Emp_CustomField.")
        
        field_value = self.field_value

        if custom_field.data_type == 'dropdown':
            if custom_field.dropdown_values:
                options = custom_field.dropdown_values
                if not field_value or field_value not in options:
                    raise ValidationError({'field_value': 'Select a value from the dropdown options.'})
        
        elif custom_field.data_type == 'radio':
            if custom_field.radio_values:
                options = custom_field.radio_values
                if not field_value or field_value not in options:
                    raise ValidationError({'field_value': 'Select a value from the radio options.'})
       
        elif custom_field.data_type == 'checkbox':
            if custom_field.checkbox_values:
                options = custom_field.checkbox_values
                if not field_value or field_value not in options:
                    raise ValidationError({'field_value': 'Select a value from the checkbox options.'})


        elif custom_field.data_type == 'date':
            if field_value:
                try:
                    parts = field_value.split('-')
                    if len(parts) != 3:
                        raise ValueError
                    day, month, year = parts
                    formatted_date = f"{day.zfill(2)}-{month.zfill(2)}-{year}"
                    datetime.strptime(formatted_date, '%d-%m-%Y')
                except ValueError:
                    raise ValidationError({'field_value': 'Invalid date format. Date should be in DD-MM-YYYY format.'})
            else:
                raise ValidationError({'field_value': 'Date value is required.'})

    

#EMPLOPYEE JOB HISTORY
class EmpJobHistory(models.Model):
    emp_id                          = models.ForeignKey('emp_master',on_delete = models.CASCADE,related_name='emp_job_history')
    emp_jh_from_date                = models.DateField()
    emp_jh_end_date                 = models.DateField()
    emp_jh_company_name             = models.CharField(max_length=50)
    emp_jh_designation              = models.CharField(max_length=50)
    emp_jh_leaving_salary_permonth  = models.FloatField()
    emp_jh_reason                   = models.CharField(max_length=100)
    emp_jh_years_experiance         = models.FloatField()
    created_at                      = models.DateTimeField(auto_now_add=True)
    created_by                      = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    updated_at                      = models.DateTimeField(auto_now=True)
    updated_by                      = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_updated_by')

#EMPLOPYEE JOB HISTORY UDF
class EmpJobHistory_CustomField(models.Model):
    FIELD_TYPES = (   
        ('dropdown', 'DropdownField'),
        ('radio', 'RadioButtonField'),
        ('date', 'DateField'),
        ('text', 'TextField'),
        ('checkbox', 'CheckboxField'),
    )
    # emp_master = models.ForeignKey(emp_master, on_delete=models.CASCADE, related_name='custom_fields',null=True)
    emp_custom_field = models.CharField(unique=True,max_length=100,null=True)  # Field name provided by end user
    data_type        = models.CharField(max_length=20, choices=FIELD_TYPES, null=True, blank=True)
    dropdown_values  = models.JSONField(null=True, blank=True)
    radio_values     = models.JSONField(null=True, blank=True)
    checkbox_values  = models.JSONField(null=True,blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    created_by       = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    def __str__(self):
        return self.emp_custom_field    
    
    def clean(self):
        # Validate dropdown field values
        if self.data_type == 'dropdown':
            if self.dropdown_values:
                options = self.dropdown_values
                if  not  options:
                    raise ValidationError({'field_value': 'Select a value from the dropdown options.'})
            else:
                raise ValidationError({'field_value': 'provide value to the dropdown options.'})
        # Validate radio field values
        elif self.data_type == 'radio':
            if self.radio_values:
                options = self.radio_values
                if not  options:
                    raise ValidationError({'field_value': 'Select a value from the radio options.'})
            else:
                raise ValidationError({'field_value': 'provide value to the radio options.'})
        # Validate checkbox field values
        elif self.data_type == 'checkbox':
            if self.checkbox_values:
                options = self.checkbox_values
                if not  options:
                    raise ValidationError({'field_value': 'Select a value from the checkbox options.'})
            else:
                raise ValidationError({'field_value': 'provide value to the checkbox options.'})
    def save(self, *args, **kwargs):
        self.clean()  # Call clean to perform validation
        super().save(*args, **kwargs)
    
class JobHistory_CustomFieldValue(models.Model):
    emp_custom_field = models.CharField(max_length=100)
    field_value      = models.TextField(null=True, blank=True)  # Field value provided by end user
    emp_job_history  = models.ForeignKey(EmpJobHistory, on_delete=models.CASCADE,related_name='custom_field_values')
    created_at       = models.DateTimeField(auto_now_add=True)
    # created_by       = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
 

    def __str__(self):
        return f'{self.emp_custom_field.emp_custom_field}: {self.field_value}'

    def save(self, *args, **kwargs):
        if not self.emp_custom_field:
            raise ValueError("Field name cannot be None or empty.")
        if not EmpJobHistory_CustomField.objects.filter(emp_custom_field=self.emp_custom_field).exists():
            raise ValueError(f"Field name '{self.emp_custom_field}' does not exist in Emp_CustomField.")

        # Check if a custom field value already exists for the same emp_master and emp_custom_field
        existing_value = JobHistory_CustomFieldValue.objects.filter(
            emp_custom_field=self.emp_custom_field,
            emp_job_history=self.emp_job_history
        ).first()

        if existing_value:
            # If it exists, update the existing record instead of creating a new one
            existing_value.field_value = self.field_value
            # Use update() to avoid calling save() and prevent recursion
            JobHistory_CustomFieldValue.objects.filter(
                id=existing_value.id
            ).update(field_value=self.field_value)
        else:
            # Call full_clean to ensure that the clean method is called
            self.full_clean()
            super().save(*args, **kwargs)

    def clean(self):
        # Retrieve the custom field object
        custom_field = EmpJobHistory_CustomField.objects.filter(emp_custom_field=self.emp_custom_field).first()

        if not custom_field:
            raise ValidationError(f"Field name '{self.emp_custom_field}' does not exist in Emp_CustomField.")
        
        field_value = self.field_value

        if custom_field.data_type == 'dropdown':
            if custom_field.dropdown_values:
                options = custom_field.dropdown_values
                if not field_value or field_value not in options:
                    raise ValidationError({'field_value': 'Select a value from the dropdown options.'})
        
        elif custom_field.data_type == 'radio':
            if custom_field.radio_values:
                options = custom_field.radio_values
                if not field_value or field_value not in options:
                    raise ValidationError({'field_value': 'Select a value from the radio options.'})
       
        elif custom_field.data_type == 'checkbox':
            if custom_field.checkbox_values:
                options = custom_field.checkbox_values
                if not field_value or field_value not in options:
                    raise ValidationError({'field_value': 'Select a value from the checkbox options.'})


        elif custom_field.data_type == 'date':
            if field_value:
                try:
                    parts = field_value.split('-')
                    if len(parts) != 3:
                        raise ValueError
                    day, month, year = parts
                    formatted_date = f"{day.zfill(2)}-{month.zfill(2)}-{year}"
                    datetime.strptime(formatted_date, '%d-%m-%Y')
                except ValueError:
                    raise ValidationError({'field_value': 'Invalid date format. Date should be in DD-MM-YYYY format.'})
            else:
                raise ValidationError({'field_value': 'Date value is required.'})
    
    

#EMPLOYEE QUALIFICATION
class EmpQualification(models.Model):
    emp_id                = models.ForeignKey('emp_master',on_delete = models.CASCADE,related_name='emp_qualification')
    emp_qualification     = models.CharField(max_length=50)
    emp_qf_instituition   = models.CharField(max_length=50)
    emp_qf_year           = models.DateField()
    emp_qf_subject        = models.CharField(max_length=50)
    created_at            = models.DateTimeField(auto_now_add=True)
    created_by            = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    updated_at            = models.DateTimeField(auto_now=True)
    updated_by            = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_updated_by')
#EMPLOYEE QUALIFICATION UDF
class EmpQualification_CustomField(models.Model):
    FIELD_TYPES = (   
        ('dropdown', 'DropdownField'),
        ('radio', 'RadioButtonField'),
        ('date', 'DateField'),
        ('text', 'TextField'),
        ('checkbox', 'CheckboxField'),
    )
    # emp_master = models.ForeignKey(emp_master, on_delete=models.CASCADE, related_name='custom_fields',null=True)
    emp_custom_field = models.CharField(unique=True,max_length=100,null=True)  # Field name provided by end user
    data_type        = models.CharField(max_length=20, choices=FIELD_TYPES, null=True, blank=True)
    dropdown_values  = models.JSONField(null=True, blank=True)
    radio_values     = models.JSONField(null=True, blank=True)
    checkbox_values  = models.JSONField(null=True,blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    created_by       = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    def __str__(self):
        return self.emp_custom_field    
    
    def clean(self):
        # Validate dropdown field values
        if self.data_type == 'dropdown':
            if self.dropdown_values:
                options = self.dropdown_values
                if  not  options:
                    raise ValidationError({'field_value': 'Select a value from the dropdown options.'})
            else:
                raise ValidationError({'field_value': 'provide value to the dropdown options.'})
        # Validate radio field values
        elif self.data_type == 'radio':
            if self.radio_values:
                options = self.radio_values
                if not  options:
                    raise ValidationError({'field_value': 'Select a value from the radio options.'})
            else:
                raise ValidationError({'field_value': 'provide value to the radio options.'})
        # Validate checkbox field values
        elif self.data_type == 'checkbox':
            if self.checkbox_values:
                options = self.checkbox_values
                if not  options:
                    raise ValidationError({'field_value': 'Select a value from the checkbox options.'})
            else:
                raise ValidationError({'field_value': 'provide value to the checkbox options.'})
    def save(self, *args, **kwargs):
        self.clean()  # Call clean to perform validation
        super().save(*args, **kwargs)
    
class Qualification_CustomFieldValue(models.Model):
    emp_custom_field = models.CharField(max_length=100)
    field_value      = models.TextField(null=True, blank=True)  # Field value provided by end user
    emp_qualification    = models.ForeignKey(EmpQualification, on_delete=models.CASCADE,related_name='custom_field_values')
    created_at       = models.DateTimeField(auto_now_add=True)
    # created_by       = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
 

    def __str__(self):
        return f'{self.emp_custom_field.emp_custom_field}: {self.field_value}'

    def save(self, *args, **kwargs):
        if not self.emp_custom_field:
            raise ValueError("Field name cannot be None or empty.")
        if not EmpQualification_CustomField.objects.filter(emp_custom_field=self.emp_custom_field).exists():
            raise ValueError(f"Field name '{self.emp_custom_field}' does not exist in Emp_CustomField.")

        # Check if a custom field value already exists for the same emp_master and emp_custom_field
        existing_value = Qualification_CustomFieldValue.objects.filter(
            emp_custom_field=self.emp_custom_field,
            emp_qualification=self.emp_qualification
        ).first()

        if existing_value:
            # If it exists, update the existing record instead of creating a new one
            existing_value.field_value = self.field_value
            # Use update() to avoid calling save() and prevent recursion
            Qualification_CustomFieldValue.objects.filter(
                id=existing_value.id
            ).update(field_value=self.field_value)
        else:
            # Call full_clean to ensure that the clean method is called
            self.full_clean()
            super().save(*args, **kwargs)

    def clean(self):
        # Retrieve the custom field object
        custom_field = EmpQualification_CustomField.objects.filter(emp_custom_field=self.emp_custom_field).first()

        if not custom_field:
            raise ValidationError(f"Field name '{self.emp_custom_field}' does not exist in Emp_CustomField.")
        
        field_value = self.field_value

        if custom_field.data_type == 'dropdown':
            if custom_field.dropdown_values:
                options = custom_field.dropdown_values
                if not field_value or field_value not in options:
                    raise ValidationError({'field_value': 'Select a value from the dropdown options.'})
        
        elif custom_field.data_type == 'radio':
            if custom_field.radio_values:
                options = custom_field.radio_values
                if not field_value or field_value not in options:
                    raise ValidationError({'field_value': 'Select a value from the radio options.'})
       
        elif custom_field.data_type == 'checkbox':
            if custom_field.checkbox_values:
                options = custom_field.checkbox_values
                if not field_value or field_value not in options:
                    raise ValidationError({'field_value': 'Select a value from the checkbox options.'})


        elif custom_field.data_type == 'date':
            if field_value:
                try:
                    parts = field_value.split('-')
                    if len(parts) != 3:
                        raise ValueError
                    day, month, year = parts
                    formatted_date = f"{day.zfill(2)}-{month.zfill(2)}-{year}"
                    datetime.strptime(formatted_date, '%d-%m-%Y')
                except ValueError:
                    raise ValidationError({'field_value': 'Invalid date format. Date should be in DD-MM-YYYY format.'})
            else:
                raise ValidationError({'field_value': 'Date value is required.'})
            

class document_type(models.Model):
    branch      = models.ManyToManyField('OrganisationManager.brnch_mstr', blank=True)
    type_name   = models.CharField(max_length=50,unique=True)
    description = models.CharField(max_length=200)
    is_active   = models.BooleanField(default=True)  # Add is_active field
    def __str__(self):
        return self.type_name
    def save(self, *args, **kwargs):
        if not self.pk:  # Only set is_active=True on creation
            self.is_active = True
        super().save(*args, **kwargs)
    
    
    


#EMPLOYEE DOCUMENTS
from django.db import models
from django.utils import timezone
from django.db.models.signals import post_save
from django.dispatch import receiver
#EMPLOYEE DOCUMENTS
class Emp_Documents(models.Model):
    emp_id               =models.ForeignKey('emp_master',on_delete = models.CASCADE,related_name='emp_documents')
    document_type        = models.ForeignKey('document_type',on_delete = models.CASCADE,null=True,blank=True)
    emp_doc_number       = models.CharField(max_length=50,unique=True)
    emp_doc_issued_date  = models.DateField()
    emp_doc_expiry_date  = models.DateField()
    emp_doc_document     = models.FileField(upload_to="emp_documents/",null=True,blank=True)
    is_active            = models.BooleanField(default=True)
    created_at           = models.DateTimeField(auto_now_add=True)
    created_by           = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    updated_at           = models.DateTimeField(auto_now=True)
    updated_by           = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_updated_by')
    
   
    def __str__(self):
        return f"{self.document_type} - {self.emp_id}" 
    def save(self, *args, **kwargs):
        from .models import notification

        is_new = self.pk is None
        old_expiry = None

        if not is_new:
            try:
                old_doc = Emp_Documents.objects.get(pk=self.pk)
                old_expiry = old_doc.emp_doc_expiry_date
            except Emp_Documents.DoesNotExist:
                pass

        super().save(*args, **kwargs)

        # If expiry date changed → remove old notifications
        if not is_new and old_expiry and old_expiry != self.emp_doc_expiry_date:
            notification.objects.filter(document_id=self).delete()
            # re-check expiry with updated date
            check_document_expiry_and_notify(self)

        # For newly created documents
        if is_new:
            check_document_expiry_and_notify(self)


def check_document_expiry_and_notify(document):
    from .tasks import send_document_notification, send_template_email
    today = timezone.now().date()
    expiry_date = document.emp_doc_expiry_date
    employee = document.emp_id
    branch = employee.emp_branch_id

    if not branch:
        return

    settings = None  

    try:
        settings = NotificationSettings.objects.get(branch=branch)
        days_before = settings.days_before_expiry
        ess_users = settings.notify_users.all()
    except NotificationSettings.DoesNotExist:
        days_before = 7
        ess_users = []

    days_until_expiry = (expiry_date - today).days
    status = None

    if expiry_date <= today:
        status = 'expired or expiring today'
    elif days_until_expiry <= days_before:
        status = f'expiring in {days_until_expiry} days'

    if not status:
        return  # No notification needed

    # 🔹 Notify Employee
    send_document_notification(document, expiry_date, status, settings)

    # 🔹 Notify ESS Users
    if ess_users:
        doc_message = (
            f"{employee.emp_first_name} {employee.emp_last_name} - "
            f"{document.document_type} is {status} on {expiry_date}"
        )
        for ess_user in ess_users:
            context = {
                'ess_user_first_name': ess_user.username,
                'documents': doc_message
            }
            send_template_email('ESS User Notification', ess_user.email, context)
            
#Document UDF
class EmpDocuments_CustomField(models.Model):
    FIELD_TYPES = (   
        ('dropdown', 'DropdownField'),
        ('radio', 'RadioButtonField'),
        ('date', 'DateField'),
        ('text', 'TextField'),
        ('checkbox', 'CheckboxField'),
    )
    # emp_master = models.ForeignKey(emp_master, on_delete=models.CASCADE, related_name='custom_fields',null=True)
    emp_custom_field = models.CharField(unique=True,max_length=100,null=True)  # Field name provided by end user
    data_type        = models.CharField(max_length=20, choices=FIELD_TYPES, null=True, blank=True)
    dropdown_values  = models.JSONField(null=True, blank=True)
    radio_values     = models.JSONField(null=True, blank=True)
    checkbox_values  = models.JSONField(null=True,blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    created_by       = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    def __str__(self):
        return self.emp_custom_field    
    
    def clean(self):
        # Validate dropdown field values
        if self.data_type == 'dropdown':
            if self.dropdown_values:
                options = self.dropdown_values
                if  not  options:
                    raise ValidationError({'field_value': 'Select a value from the dropdown options.'})
            else:
                raise ValidationError({'field_value': 'provide value to the dropdown options.'})
        # Validate radio field values
        elif self.data_type == 'radio':
            if self.radio_values:
                options = self.radio_values
                if not  options:
                    raise ValidationError({'field_value': 'Select a value from the radio options.'})
            else:
                raise ValidationError({'field_value': 'provide value to the radio options.'})
        # Validate checkbox field values
        elif self.data_type == 'checkbox':
            if self.checkbox_values:
                options = self.checkbox_values
                if not  options:
                    raise ValidationError({'field_value': 'Select a value from the checkbox options.'})
            else:
                raise ValidationError({'field_value': 'provide value to the checkbox options.'})
    def save(self, *args, **kwargs):
        self.clean()  # Call clean to perform validation
        super().save(*args, **kwargs)
    
class Doc_CustomFieldValue(models.Model):
    emp_custom_field = models.CharField(max_length=100)
    field_value      = models.TextField(null=True, blank=True)  # Field value provided by end user
    emp_documents       = models.ForeignKey('Emp_Documents', on_delete=models.CASCADE, related_name='custom_field_values')
    created_at       = models.DateTimeField(auto_now_add=True)
    # created_by       = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
 

    def __str__(self):
        return f'{self.emp_custom_field.emp_custom_field}: {self.field_value}'

    def save(self, *args, **kwargs):
        if not self.emp_custom_field:
            raise ValueError("Field name cannot be None or empty.")
        if not EmpDocuments_CustomField.objects.filter(emp_custom_field=self.emp_custom_field).exists():
            raise ValueError(f"Field name '{self.emp_custom_field}' does not exist in Emp_CustomField.")

        # Check if a custom field value already exists for the same emp_master and emp_custom_field
        existing_value = Doc_CustomFieldValue.objects.filter(
            emp_custom_field=self.emp_custom_field,
            emp_documents=self.emp_documents
        ).first()

        if existing_value:
            # If it exists, update the existing record instead of creating a new one
            existing_value.field_value = self.field_value
            # Use update() to avoid calling save() and prevent recursion
            Doc_CustomFieldValue.objects.filter(
                id=existing_value.id
            ).update(field_value=self.field_value)
        else:
            # Call full_clean to ensure that the clean method is called
            self.full_clean()
            super().save(*args, **kwargs)

    def clean(self):
        # Retrieve the custom field object
        custom_field = EmpDocuments_CustomField.objects.filter(emp_custom_field=self.emp_custom_field).first()

        if not custom_field:
            raise ValidationError(f"Field name '{self.emp_custom_field}' does not exist in Emp_CustomField.")
        
        field_value = self.field_value

        if custom_field.data_type == 'dropdown':
            if custom_field.dropdown_values:
                options = custom_field.dropdown_values
                if not field_value or field_value not in options:
                    raise ValidationError({'field_value': 'Select a value from the dropdown options.'})
        
        elif custom_field.data_type == 'radio':
            if custom_field.radio_values:
                options = custom_field.radio_values
                if not field_value or field_value not in options:
                    raise ValidationError({'field_value': 'Select a value from the radio options.'})
       
        elif custom_field.data_type == 'checkbox':
            if custom_field.checkbox_values:
                options = custom_field.checkbox_values
                if not field_value or field_value not in options:
                    raise ValidationError({'field_value': 'Select a value from the checkbox options.'})


        elif custom_field.data_type == 'date':
            if field_value:
                try:
                    parts = field_value.split('-')
                    if len(parts) != 3:
                        raise ValueError
                    day, month, year = parts
                    formatted_date = f"{day.zfill(2)}-{month.zfill(2)}-{year}"
                    datetime.strptime(formatted_date, '%d-%m-%Y')
                except ValueError:
                    raise ValidationError({'field_value': 'Invalid date format. Date should be in DD-MM-YYYY format.'})
            else:
                raise ValidationError({'field_value': 'Date value is required.'})
    
    
    
# Display document type name and employee ID  
class EmpLeaveRequest(models.Model):
    employee    = models.ForeignKey('emp_master', on_delete=models.CASCADE,related_name='emp_leaverequest')
    start_date  = models.DateField()
    end_date    = models.DateField()
    status      = models.CharField(max_length=20, default='Pending')
    reason      = models.CharField(max_length=150,default='its ook')




class notification(models.Model):
    # notified_emp =models.ForeignKey('EmpManagement.emp_master',on_delete=models.CASCADE)
    message      = models.CharField(max_length=200)
    created_at   = models.DateTimeField(auto_now_add=True)
    document_id  = models.ForeignKey('Emp_Documents',on_delete = models.CASCADE,null=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    created_by   = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    
class EmployeeMarketingSkill(models.Model):
    emp_id           = models.ForeignKey('emp_master',on_delete = models.CASCADE,related_name='emp_market_skills')
    marketing_skill  = models.ForeignKey(MarketingSkill, on_delete=models.SET_NULL, null=True, blank=True)
    percentage       = models.DecimalField(max_digits=5, decimal_places=2, default=None, null=True, blank=True)
    value            = models.CharField(max_length=100,null=True,blank =True,default=None)
    created_at       = models.DateTimeField(auto_now_add=True)
    created_by       = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    

    def __str__(self):
        return f"{self.emp_id} - {self.value}"
@receiver(pre_save, sender=EmployeeMarketingSkill)
def update_value_field(sender, instance, **kwargs):
    if instance.marketing_skill:
        instance.value = instance.marketing_skill.marketing
class EmployeeProgramSkill(models.Model):
    emp_id        =models.ForeignKey('emp_master',on_delete = models.CASCADE,related_name='emp_prgrm_skills')
    program_skill = models.ForeignKey(ProgrammingLanguageSkill, on_delete=models.SET_NULL, null=True, blank=True)
    percentage    = models.DecimalField(max_digits=5, decimal_places=2, default=None, null=True, blank=True)
    value         = models.CharField(max_length=100,null=True,blank =True,default=None)
    created_at    = models.DateTimeField(auto_now_add=True)
    created_by    = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    def __str__(self):
        return f"{self.emp_id} - {self.value}"
@receiver(pre_save, sender=EmployeeProgramSkill)
def update_value_field(sender, instance, **kwargs):
    if instance.program_skill:
        instance.value = instance.program_skill.programming_language
class EmployeeLangSkill(models.Model):
    emp_id          = models.ForeignKey('emp_master',on_delete = models.CASCADE,related_name='emp_lang_skills')
    language_skill  = models.ForeignKey(LanguageSkill, on_delete=models.SET_NULL, null=True, blank=True)
    percentage      = models.DecimalField(max_digits=5, decimal_places=2, default=None, null=True, blank=True)
    value           = models.CharField(max_length=100,null=True,blank =True,default=None)
    created_at      = models.DateTimeField(auto_now_add=True)
    created_by      = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')


    def __str__(self):
        return f"{self.emp_id} - {self.value}"
@receiver(pre_save, sender=EmployeeLangSkill)
def update_value_field(sender, instance, **kwargs):
    if instance.language_skill:
        instance.value = instance.language_skill.language


### EmailTemplate Model ###
class EmailTemplate(models.Model):
    template_type = models.CharField(max_length=50, choices=[
        ('request_created', 'Request Created'),
        ('request_approved', 'Request Approved'),
        ('request_rejected', 'Request Rejected')
    ])
    subject             = models.CharField(max_length=255)
    body                = models.TextField()
    created_at          = models.DateTimeField(auto_now_add=True)
    created_by          = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    branch              = models.ManyToManyField('OrganisationManager.brnch_mstr',blank=True)
    Department          = models.ManyToManyField('OrganisationManager.dept_master',blank=True)
    Category            = models.ManyToManyField('OrganisationManager.ctgry_master',blank=True)
    Designation         = models.ManyToManyField('OrganisationManager.desgntn_master',blank=True)
    def __str__(self):
        return f"{self.template_type} - {self.subject}"


class EmailConfiguration(models.Model):
    email_host            = models.CharField(max_length=255, default='smtp.gmail.com')
    email_port            = models.IntegerField(default=587)
    email_use_tls         = models.BooleanField(default=True)
    email_host_user       = models.CharField(max_length=255, blank=True, null=True)
    email_host_password   = models.CharField(max_length=255, blank=True, null=True)
    is_active             = models.BooleanField(default=False)
    created_at            = models.DateTimeField(auto_now_add=True)
    created_by            = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')


    def __str__(self):
        return f"Email Configuration ({'Active' if self.is_active else 'Inactive'})"
    def save(self, *args, **kwargs):
        if self.is_active:
            # Deactivate other configurations
            EmailConfiguration.objects.filter(is_active=True).update(is_active=False)
        super().save(*args, **kwargs)

class RequestNotification(models.Model):
    recipient_user     = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True,on_delete=models.CASCADE)
    recipient_employee = models.ForeignKey('emp_master', null=True, blank=True, on_delete=models.CASCADE)
    message            = models.CharField(max_length=255)
    created_at         = models.DateTimeField(auto_now_add=True)
    is_read            = models.BooleanField(default=False)
    deligate_user      = models.ForeignKey('UserManagement.CustomUser',null=True,blank=True,on_delete=models.CASCADE,related_name='deligated_notifications')
    is_deligate        = models.BooleanField(default=False)
    def __str__(self):
        if self.recipient_user:
            return f"Notification for {self.recipient_user.username}: {self.message}"
        else:
            return f"Notification for employee: {self.message}"    
    
class RequestType(models.Model):
    name                =  models.CharField(max_length=50,unique=True)
    description         = models.CharField(max_length=150)
    created_at          = models.DateField(auto_now_add=True)
    updated_at          = models.DateField(auto_now_add=True)
    created_by          = models.ForeignKey('UserManagement.CustomUser',on_delete=models.CASCADE)
    use_common_workflow = models.BooleanField(default=False)
    salary_component = models.ForeignKey('PayrollManagement.SalaryComponent', on_delete=models.SET_NULL,null=True, blank=True,help_text="Link to salary component for payroll integration")
    min_approvals_required = models.PositiveIntegerField(null=True, blank=True, help_text="Minimum number of approvals required to approve the request")
    branch = models.ManyToManyField('OrganisationManager.brnch_mstr',blank=True)

    
    def __str__(self):
        return self.name

    
class CommonWorkflow(models.Model):
    level = models.IntegerField()
    role = models.CharField(max_length=50, null=True, blank=True)
    approver = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True, on_delete=models.SET_NULL)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['level'], name='unique_common_workflow_level')
        ]
    def __str__(self):
        return f"Level {self.level} - {self.role or self.approver}"


class GeneralRequest(models.Model):
    document_number  = models.CharField(max_length=50, unique=True, blank=True)
    reason           =  models. CharField(max_length=200)
    branch           =  models.ForeignKey('OrganisationManager.brnch_mstr',on_delete = models.CASCADE)
    request_type     =  models.ForeignKey('RequestType',on_delete = models.CASCADE)
    employee         =  models.ForeignKey('emp_master',on_delete = models.CASCADE)
    total            =  models.IntegerField(null=True)
    status           =  models.CharField(max_length=20, default='Pending')
    remarks          =  models.CharField(max_length=50, null=True, blank=True)
    request_document =  models.FileField(upload_to="generalrequest_documents/",null=True,blank=True)
    is_processed     =  models.BooleanField(default=False, help_text="Marks whether this request has been processed in payroll")
    created_by       =  models.ForeignKey('UserManagement.CustomUser',on_delete=models.CASCADE,null=True,blank=True)
    created_at_date  =  models.DateField(auto_now_add=True)
    def __str__(self):
        return f"{self.document_number}-{self.request_type.name}"
    
    def get_employee_requests(employee_id):
        return GeneralRequest.objects.filter(employee_id=employee_id).order_by('-created_at_date')

    def move_to_next_level(self):

        # ---------------- REJECT ----------------
        if self.approvals.filter(status=Approval.REJECTED).exists():
            self.status = 'Rejected'
            self.save()

            # send_notification_email(
            #     user=self.created_by,
            #     employee=self.employee,
            #     message=f"Your request has been rejected.",
            #     template_type="request_rejected",
            #     context={
            #         **get_employee_context(self.employee),
            #         'request': str(self),
            #     },
            #     email_template_model=EmailTemplate,
            #     notification_model=RequestNotification,
            # )
            send_notification_email(
                user=self.created_by,
                employee=self.employee,

                branch=self.branch,

                title="Request Rejected",

                notification_type="general",

                message=(f"Your GeneralRequest {self.request_type}"
                        f"(Document No: {self.document_number}) has been Rejected."
                    ),

                template_type="request_rejected",

                context={
                    **get_employee_context(self.employee),
                    'request': str(self),
                },

                email_template_model=EmailTemplate,

                notification_model=RequestNotification,
            )
            return

        # ---------------- GET WORKFLOW ----------------
        workflow = ApprovalWorkflow.objects.filter(
            request_type=self.request_type,
        ).first()

        if not workflow:
            workflow = ApprovalWorkflow.objects.create(
                request_type=self.request_type,
                branch=self.employee.emp_branch_id,
                approval_type='no_approval'
            )

            ApprovalLevel.objects.create(
                workflow=workflow,
                level=1,
                role="Auto Level",
                approver=None
            )

        approval_type = workflow.approval_type

        # =========================================================
        # MINIMUM APPROVAL CHECK
        # =========================================================
        approved_count = self.approvals.filter(status=Approval.APPROVED).count()
        min_required = getattr(self.request_type, 'min_approvals_required', None)

        if min_required and approved_count >= min_required:
            self.status = 'Approved'
            self.save()

            # send_notification_email(
            #     user=self.created_by,
            #     employee=self.employee,
            #     message=f"Your request has been approved.",
            #     template_type="request_approved",
            #     context={
            #         **get_employee_context(self.employee),
            #         'request': str(self),
            #     },
            #     email_template_model=EmailTemplate,
            #     notification_model=RequestNotification,
            # )
            send_notification_email(
                user=self.created_by,
                employee=self.employee,

                branch=self.branch,

                title="Request Approved",

                notification_type="general",

                message=(f"Your GeneralRequest {self.request_type}"
                        f"(Document No: {self.document_number}) has been Approved."
                    ),

                template_type="request_approved",

                context={
                    **get_employee_context(self.employee),
                    'request': str(self),
                },

                email_template_model=EmailTemplate,

                notification_model=RequestNotification,
            )
            return
            

        # =========================================================
        # NO APPROVAL
        # =========================================================
        if approval_type == 'no_approval':
            self.status = 'Approved'
            self.save()

            # send_notification_email(
            #     user=self.created_by,
            #     employee=self.employee,
            #     message=f"Your request has been auto approved.",
            #     template_type="request_approved",
            #     context={
            #         **get_employee_context(self.employee),
            #         'request': str(self),
            #     },
            #     email_template_model=EmailTemplate,
            #     notification_model=RequestNotification,
            # )
            send_notification_email(
                user=self.created_by,
                employee=self.employee,

                branch=self.branch,

                title="Request Approved",

                notification_type="general",

                message=(f"Your GeneralRequest {self.request_type}"
                        f"(Document No: {self.document_number}) has been AutoApproved."
                    ),

                template_type="request_approved",

                context={
                    **get_employee_context(self.employee),
                    'request': str(self),
                },

                email_template_model=EmailTemplate,

                notification_model=RequestNotification,
            )
            return
            

        # =========================================================
        # REPORTING MANAGER
        # =========================================================
        if approval_type == 'reporting_manager':

            if self.approvals.filter(status=Approval.APPROVED).exists():
                self.status = 'Approved'
                self.save()

                # send_notification_email(
                #     user=self.created_by,
                #     employee=self.employee,
                #     message=f"Your request has been approved by reporting manager.",
                #     template_type="request_approved",
                #     context={
                #         **get_employee_context(self.employee),
                #         'request': str(self),
                #     },
                #     email_template_model=EmailTemplate,
                #     notification_model=RequestNotification,
                # )
                send_notification_email(
                user=self.created_by,
                employee=self.employee,

                branch=self.branch,

                title="Request Approved",

                notification_type="general",

                message=(f"Your GeneralRequest {self.request_type}"
                        f"(Document No: {self.document_number}) has been Approved by ReportingManager."
                    ),


                template_type="request_approved",

                context={
                    **get_employee_context(self.employee),
                    'request': str(self),
                },

                email_template_model=EmailTemplate,

                notification_model=RequestNotification,
            )
            
                

            return

        # =========================================================
        # MULTI APPROVAL
        # =========================================================

        last_approved = self.approvals.filter(
            status=Approval.APPROVED
        ).order_by('-level').first()

        current_level = (last_approved.level + 1) if last_approved else 1

        if self.approvals.filter(level=current_level).exists():
            return

        next_level = workflow.levels.filter(level=current_level).first()

        if next_level and next_level.approver:

            Approval.objects.create(
                general_request=self,
                approver=next_level.approver,
                role=next_level.role,
                level=next_level.level,
                status=Approval.PENDING
            )

            # send_notification_email(
            #     user=next_level.approver,
            #     employee=None,
            #     message=f"New request waiting for your approval.",
            #     template_type="request_created",
            #     context={
            #         **get_employee_context(self.employee),
            #         'request': str(self),
            #     },
            #     email_template_model=EmailTemplate,
            #     notification_model=RequestNotification,
            # )
            send_notification_email(
                user=next_level.approver,
                employee=None,

                branch=self.branch,

                title="Request Created",

                notification_type="general",

                message=(f"A GeneralRequest {self.request_type} "
                         f"(Document No: {self.document_number}) is waiting your approval."
                ),

                template_type="request_created",

                context={
                    **get_employee_context(self.employee),
                    'request': str(self),
                },

                email_template_model=EmailTemplate,

                notification_model=RequestNotification,
            )
            

        else:
            self.status = 'Approved'
            self.save()

            # send_notification_email(
            #     user=self.created_by,
            #     employee=self.employee,
            #     message=f"Your request has been fully approved.",
            #     template_type="request_approved",
            #     context={
            #         **get_employee_context(self.employee),
            #         'request': str(self),
            #     },
            #     email_template_model=EmailTemplate,
            #     notification_model=RequestNotification,
            # )
            send_notification_email(
                user=self.created_by,
                employee=self.employee,

                branch=self.branch,

                title="Request Approved",

                notification_type="general",

                message=(f"A Generalrequest {self.request_type}"
                        f"(Document No: {self.document_number}) has been fully Approved."
                        ),

                template_type="request_approved",

                context={
                    **get_employee_context(self.employee),
                    'request': str(self),
                },

                email_template_model=EmailTemplate,

                notification_model=RequestNotification,
            )
                       

class ApprovalWorkflow(models.Model):
    APPROVAL_TYPE_CHOICES = [
        ('no_approval', 'No Approval'),
        ('reporting_manager', 'Reporting Manager'),
        ('multi_approval', 'Multi Approval'),
    ]
    request_type = models.ForeignKey('RequestType', related_name='approval_workflows', on_delete=models.CASCADE)
    branch       = models.ManyToManyField('OrganisationManager.brnch_mstr', blank=True)
    approval_type = models.CharField(
        max_length=30,
        choices=APPROVAL_TYPE_CHOICES,
        default='no_approval'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='workflow_created_by')

    def __str__(self):
        return f"Workflow for {self.request_type.name}"

class ApprovalLevel(models.Model):
    workflow = models.ForeignKey(ApprovalWorkflow, related_name='levels', on_delete=models.CASCADE, null=True)
    level = models.IntegerField()
    role = models.CharField(max_length=50, null=True, blank=True)  # Use this for role-based approval like 'CEO' or 'Manager'
    approver = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True, on_delete=models.SET_NULL)  # Use this for user-based approval
    
    # 🆕 Escalation fields
    escalate_to = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True, blank=True,related_name='escalated_levels')
    escalate_after_days = models.PositiveIntegerField(default=0, help_text="Escalate after X days if pending")
    escalate_after_hours = models.PositiveIntegerField(default=0, help_text="Escalate after X hours if pending")
    escalate_after_minutes = models.PositiveIntegerField(default=0, help_text="Escalate after X minutes if pending")

    class Meta:
        ordering = ['level']
        permissions = (
                    ("add_genrl_escalation", "Can add general Escalation"),
                    ("view_genrl_escalation", "Can view general Escalation"),
                    ("change_genrl_escalation", "Can change general Escalation"),
                    ("export_genrl_escalation", "Can export general Escalation"),
                    ("delete_genrl_escalation", "Can delete general Escalation"),
            )
        
    def get_escalation_timedelta(self):
        """Returns the total time delta for escalation."""
        from datetime import timedelta
        total_minutes = (self.escalate_after_days * 24 * 60) + (self.escalate_after_hours * 60) + self.escalate_after_minutes
        return timedelta(minutes=total_minutes)
    
class Approval(models.Model):
    PENDING = 'Pending'
    APPROVED = 'Approved'
    REJECTED = 'Rejected'
    ESCALATED = 'Escalated'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (APPROVED, 'Approved'),
        (REJECTED, 'Rejected'),
        (ESCALATED, 'Escalated'),
    ]
    general_request = models.ForeignKey(GeneralRequest, related_name='approvals', on_delete=models.CASCADE)
    approver        = models.ForeignKey('UserManagement.CustomUser', on_delete=models.CASCADE)
    role            = models.CharField(max_length=50, null=True, blank=True)
    level           = models.IntegerField(default=1)
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES,default=PENDING)
    note            = models.TextField(null=True, blank=True)
    deligate_to     = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,blank=True,related_name='deligations_received')
    is_deligate     = models.BooleanField(default=False)
    deligate_response = models.TextField(null=True, blank=True)
    escalated = models.BooleanField(default=False)
    escalated_at = models.DateTimeField(null=True, blank=True)
    is_escalation = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by      = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    updated_at      = models.DateField(auto_now=True)

    def approve(self,note=None):
        self.status = self.APPROVED
        if note:
            self.note = note

        self.save()
        self.general_request.move_to_next_level()
    def reject(self, note=None):
        self.status = self.REJECTED
        if note:
            self.note = note
        self.save()
        self.general_request.status = 'Rejected'
        self.general_request.save()
    #     send_notification_email(
    #     user=self.general_request.created_by,
    #     employee=self.general_request.employee,
    #     message=f"Your request {self.general_request.document_number} has been rejected.",
    #     template_type="request_rejected",
    #     context={
    #         **get_employee_context(self.general_request.employee),
    #         'doc_number': self.general_request.document_number,
    #         'request_type': self.general_request.request_type.name,
    #         'rejection_reason': self.note or 'Rejected'
    #     },
    #     email_template_model=EmailTemplate,
    #     notification_model=RequestNotification
    # )
        send_notification_email(
                user=self.general_request.created_by,
                employee=self.general_request.employee,

                branch=self.general_request.branch,

                title="Request Rejected",

                notification_type="general",

                message=(f"A GeneralRequest {self.general_request.request_type}"
                        f"(Document No: {self.general_request.document_number}) has been Rejected."
                        ),

                template_type="request_rejected",

                context={
                    **get_employee_context(self.general_request.employee),
                    'request': str(self),
                    'doc_number': self.general_request.document_number,
                    'request_type': self.general_request.request_type.name,
                    'rejection_reason': self.note or 'Rejected'
                },

                email_template_model=EmailTemplate,

                notification_model=RequestNotification,
            )
@receiver(post_save, sender=GeneralRequest)
def create_initial_approval(sender, instance, created, **kwargs):
    if not created:
        return
    
    with transaction.atomic():

        if instance.request_type.use_common_workflow:
            first_level = CommonWorkflow.objects.order_by('level').first()
            workflow = None
        else:
            workflow = ApprovalWorkflow.objects.filter(
                request_type=instance.request_type,
                branch=instance.employee.emp_branch_id
                ).first()
            
            if not workflow:
                raise ValidationError(
                    f"No Approval Workflow configured for '{instance.request_type.name}'."
                )

            first_level = workflow.levels.order_by('level').first()

            if not first_level:
                raise ValidationError(
                    f"No Approval Level configured for '{instance.request_type.name}'."
                )
                
        approval_type = workflow.approval_type if workflow else 'no_approval'

        # ---------------- NO APPROVAL ----------------
        if approval_type == 'no_approval':
            # approver = instance.employee.users or instance.created_by
            Approval.objects.create(
                general_request=instance,
                # approver=approver,
                role="Auto Approval",
                level=1,
                status=Approval.APPROVED
            )

            instance.status = "Approved"
            instance.save(update_fields=["status"])

            # ---------------- EMAIL + NOTIFICATION ----------------
            # send_notification_email(
            #     user=approver,
            #     employee=instance.employee,
            #     message=f"Your request {instance.request_type} has been automatically approved.",
            #     template_type="request_approved",
            #     context={
            #         **get_employee_context(instance.employee),
            #         'request_type': instance.request_type.name
            #     },
            #     email_template_model=EmailTemplate,
            #     notification_model=RequestNotification
            # )
            send_notification_email(
                    user=instance.created_by,
                    employee=instance.employee,

                    branch=instance.employee.emp_branch_id,

                    title="Request Approved",

                    notification_type="general",

                    message=(f"A GeneralRequest {instance.request_type}"
                            f"(Document No: {instance.document_number}) has been AutoApproved ."
                        ),

                    template_type="request_approved",

                    context={
                        **get_employee_context(instance.employee),
                        'request_type': instance.request_type.name,
                    },

                    email_template_model=EmailTemplate,

                    notification_model=RequestNotification,
                )

            return

        # ---------------- REPORTING MANAGER ----------------
        if approval_type == 'reporting_manager':
            manager = getattr(instance.employee, "emp_reporting_manager", None)

            if not manager:
                raise Exception("Employee has no valid reporting manager.")

            Approval.objects.create(
                general_request=instance,
                approver=manager,
                role="Reporting Manager",
                level=1,
                status=Approval.PENDING
            )

            # ---------------- EMAIL + NOTIFICATION ----------------
            # send_notification_email(
            #     user=manager,
            #     employee=instance.employee,
            #     message=f"New request {instance.request_type} is waiting for your approval.",
            #     template_type="approval_pending",
            #     context={
            #         **get_employee_context(instance.employee),
            #         'request_type': instance.request_type.request_type
            #     },
            #     email_template_model=EmailTemplate,
            #     notification_model=RequestNotification
            # )
            send_notification_email(
                    user=manager,
                    employee=instance.employee,

                    branch=instance.employee.emp_branch_id,

                    title="Request Created",

                    notification_type="general",

                    message=(f"A GeneralRequest {instance.request_type}"
                            f"(Document No: {instance.document_number}) is waiting for your Approval"
                        ),


                    template_type="request_created",

                    context={
                        **get_employee_context(instance.employee),
                        'request_type': instance.request_type.name
                    },

                    email_template_model=EmailTemplate,

                    notification_model=RequestNotification,
                )

            return

            

        # ---------------- MULTI APPROVAL ----------------
        if approval_type == 'multi_approval':

            if first_level and first_level.approver:

                Approval.objects.create(
                    general_request=instance,
                    approver=first_level.approver,
                    role=first_level.role,
                    level=first_level.level,
                    status=Approval.PENDING
                )

                # ---------------- EMAIL + NOTIFICATION ----------------
                # send_notification_email(
                #     user=first_level.approver,
                #     employee=instance.employee,
                #     message=f"New request {instance.request_type} requires your approval.",
                #     template_type="approval_pending",
                #     context={
                #         **get_employee_context(instance.employee),
                #         'request_type': instance.request_type.request_type
                #     },
                #     email_template_model=EmailTemplate,
                #     notification_model=RequestNotification
                # )
                send_notification_email(
                    user=first_level.approver,
                    employee=instance.employee,

                    branch=instance.employee.emp_branch_id,

                    title="Request Created",

                    notification_type="general",

                    message=(f"A  GeneralRequest {instance.request_type}"
                             f"(Document No: {instance.document_number}) requires your Approval."
                        ),
                    template_type="request_created",

                    context={
                        **get_employee_context(instance.employee),
                        'request_type': instance.request_type.name
                    },

                    email_template_model=EmailTemplate,

                    notification_model=RequestNotification,
                )
                return
    
class SelectedEmpNotify(models.Model):
    # selected_ess_user = models.ForeignKey(emp_master, on_delete=models.SET_NULL, null=True, blank=True)
    # selected_ess_users=models.ManyToManyField(emp_master, blank=True)
    selected_employees = models.ManyToManyField(emp_master, blank=True)  # Allows multiple employee selections
    created_at         = models.DateTimeField(auto_now_add=True)
    created_by         = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')


class NotificationSettings(models.Model):
    branch              = models.ManyToManyField("OrganisationManager.brnch_mstr",blank=True)
    Department          = models.ManyToManyField('OrganisationManager.dept_master',blank=True)
    Category            = models.ManyToManyField('OrganisationManager.ctgry_master',blank=True)
    Designation         = models.ManyToManyField('OrganisationManager.desgntn_master',blank=True)
    notify_users        = models.ManyToManyField('UserManagement.CustomUser',blank=True )
    days_before_expiry  = models.PositiveBigIntegerField(default=7)
    days_after_expiry   = models.PositiveBigIntegerField(default=0)
    document_type        = models.ForeignKey('document_type',on_delete = models.CASCADE)
    send_email          = models.BooleanField(default=True)
    created_at          = models.DateTimeField(auto_now_add=True)
    created_by          = models.ForeignKey( 'UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,related_name='%(class)s_created_by')
    # class Meta:
    #     constraints = [
    #         models.UniqueConstraint(fields=['branch'], name='unique_branch_notification')
    #     ]

    def __str__(self):
        return f"Reminder Settings for {self.branch.name}"

class DocExpEmailTemplate(models.Model):
    template_name = models.CharField(max_length=100, choices=[
        ('Employee Notification','Employee Notification'),
        ('User Notification', 'User Notification'),
    ])
    subject         = models.CharField(max_length=255)
    body            = models.TextField()
    created_at      = models.DateTimeField(auto_now_add=True)
    created_by      = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    branch              = models.ManyToManyField('OrganisationManager.brnch_mstr',blank=True)
    Department          = models.ManyToManyField('OrganisationManager.dept_master',blank=True)
    Category            = models.ManyToManyField('OrganisationManager.ctgry_master',blank=True)
    Designation         = models.ManyToManyField('OrganisationManager.desgntn_master',blank=True)

    def __str__(self):
        return self.template_name

class EmployeeBankDetail(models.Model):
    employee = models.ForeignKey(emp_master, on_delete=models.CASCADE, related_name="bank_details")
    bank_name = models.CharField(max_length=255,blank=True, null=True)
    branch_name = models.CharField(max_length=255,blank=True, null=True)
    account_number = models.CharField(max_length=50, unique=True)
    bank_address = models.TextField(blank=True, null=True)
    route_code = models.CharField(max_length=9, validators=[RegexValidator(r'^\d{9}$', 'Must be a 9-digit number')],null=True,blank=True)
    iban_number = models.CharField(max_length=23, validators=[RegexValidator(r'^[A-Z0-9]{23}$', 'Must be a 23-character IBAN')],null=True,blank=True) # For international banking
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.employee} - {self.bank_name} ({self.account_number})"
class DocRequestEmailTemplate(models.Model):
    template_type = models.CharField(max_length=50, choices=[
        ('request_created', 'Request Created'),
        ('request_approved', 'Request Approved'),
        ('request_rejected', 'Request Rejected')
    ])
    subject             = models.CharField(max_length=255)
    body                = models.TextField()
    created_at          = models.DateTimeField(auto_now_add=True)
    created_by          = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    branch              = models.ManyToManyField('OrganisationManager.brnch_mstr',blank=True)
    Department          = models.ManyToManyField('OrganisationManager.dept_master',blank=True)
    Category            = models.ManyToManyField('OrganisationManager.ctgry_master',blank=True)
    Designation         = models.ManyToManyField('OrganisationManager.desgntn_master',blank=True)
    
    def __str__(self):
        return f"{self.template_type} - {self.subject}"
class DocRequestNotification(models.Model):
    recipient_user = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True, on_delete=models.CASCADE)
    recipient_employee = models.ForeignKey(emp_master, null=True, blank=True, on_delete=models.CASCADE, related_name='docrequest_notification')
    message = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    deligate_user = models.ForeignKey('UserManagement.CustomUser',null=True,blank=True,on_delete=models.CASCADE,related_name='doc_deligated_notifications')

    def __str__(self):
        if self.recipient_user:
            return f"Notification for {self.recipient_user.emp_code}: {self.message}"
        else:
            return f"Notification for employee: {self.message}"
class DocRequestType(models.Model):
    type_name   = models.CharField(max_length=50,unique=True)
    description = models.CharField(max_length=200)
    is_active   = models.BooleanField(default=True)  # Add is_active field
    min_approvals_required        = models.PositiveIntegerField(null=True, blank=True, help_text="Minimum number of approvals required to approve the request")
    branch = models.ManyToManyField('OrganisationManager.brnch_mstr',blank=True)

    def __str__(self):
        return self.type_name
class DocumentTemplate(models.Model):
    document_type = models.OneToOneField(DocRequestType, on_delete=models.CASCADE, related_name='document_template')
    title = models.CharField(max_length=100)
    content = models.TextField()

    def __str__(self):
        return self.title
class DocumentRequest(models.Model):
    
    document_number  = models.CharField(max_length=50, unique=True, null=True, blank=True)
    reason           = models.CharField(max_length=200)
    request_type     = models.ForeignKey(DocRequestType, on_delete=models.SET_NULL, null=True)
    branch           = models.ForeignKey("OrganisationManager.brnch_mstr", on_delete=models.CASCADE)
    employee         = models.ForeignKey('emp_master', on_delete=models.CASCADE, related_name='document_requests')
    total            = models.IntegerField(null=True)
    status           = models.CharField(max_length=20, default='Pending')
    remarks          = models.CharField(max_length=50, null=True, blank=True)
    created_by       = models.ForeignKey('UserManagement.CustomUser', on_delete=models.CASCADE, null=True, blank=True)
    created_at_date  = models.DateField(auto_now_add=True)

    def __str__(self):
        return f"{self.document_number}-{self.request_type.type_name if self.request_type else 'NoType'}"

    def move_to_next_level(self):

        # ---------------- REJECT ----------------
        if self.doc_approvals.filter(status=DocumentApproval.REJECTED).exists():
            self.status = "Rejected"
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Request Rejected",
                notification_type="document",
                message=(f"Your DocumentRequest {self.request_type}"
                        f"(Document No: {self.document_number}) has been Rejected."
                    ),
                template_type="request_rejected",
                context={
                    **get_employee_context(self.employee),
                    "doc_number": self.document_number,
                    "request_type": self.request_type.type_name,
                },
                email_template_model=DocRequestEmailTemplate,
                notification_model=DocRequestNotification,
            )
            return

        # ---------------- WORKFLOW ----------------
        workflow = DocumentApprovalWorkflow.objects.filter(
            request_type=self.request_type,
            branch__in=[self.employee.emp_branch_id]
        ).first()
        if not workflow:
            workflow = DocumentApprovalWorkflow.objects.filter(
                request_type=self.request_type
                ).first()
        if not workflow:
            return
        
        approval_type = workflow.approval_type

        # ---------------- MINIMUM APPROVAL ----------------
        approved_count = self.doc_approvals.filter(
            status=DocumentApproval.APPROVED
        ).count()

        min_required = getattr(self.request_type, "min_approvals_required", None)

        if min_required and approved_count >= min_required:

            self.status = "Approved"
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Request Approved",
                notification_type="document",
                message=(f"Your DocumentRequest {self.request_type}"
                        f"(Document No: {self.document_number}) has been Approved."
                    ),
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    "doc_number": self.document_number,
                    "request_type": self.request_type.type_name,
                },
                email_template_model=DocRequestEmailTemplate,
                notification_model=DocRequestNotification,
            )
            return

        # ---------------- NO APPROVAL ----------------
        if approval_type == "no_approval":

            self.status = "Approved"
            self.save()

            result=send_notification_email(
                user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Request Approved",
                notification_type="document",
                message=(f"Your DocumentRequest {self.request_type}"
                        f"(Document No: {self.document_number}) has been AutoApproved."
                    ),
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    "doc_number": self.document_number,
                    "request_type": self.request_type.type_name,
                },
                email_template_model=DocRequestEmailTemplate,
                notification_model=DocRequestNotification,
            )
            print(result)
            return

        # ---------------- REPORTING MANAGER ----------------
        if approval_type == "reporting_manager":

            if self.doc_approvals.filter(
                status=DocumentApproval.APPROVED
            ).exists():

                self.status = "Approved"
                self.save()

                send_notification_email(
                    user=self.created_by,
                    employee=self.employee,
                    branch=self.branch,
                    title="Request Approved",
                    notification_type="document",
                    message=(f"Your DocumentRequest {self.request_type}"
                            f"(Document No: {self.document_number}) has been Approved by ReportingManager."
                    ),
                    template_type="request_approved",
                    context={
                        **get_employee_context(self.employee),
                        "doc_number": self.document_number,
                        "request_type": self.request_type.type_name,
                    },
                    email_template_model=DocRequestEmailTemplate,
                    notification_model=DocRequestNotification,
                )

            return

        # ---------------- MULTI APPROVAL ----------------
        last_approved = self.doc_approvals.filter(
            status=DocumentApproval.APPROVED
        ).order_by("-level").first()

        current_level = (last_approved.level + 1) if last_approved else 1

        if self.doc_approvals.filter(
            level=current_level,
            status=DocumentApproval.PENDING,
        ).exists():
            return

        next_level = workflow.document_levels.filter(
            level=current_level
        ).first()

        if next_level and next_level.approver:

            DocumentApproval.objects.create(
                document_request=self,
                approver=next_level.approver,
                role=next_level.role,
                level=next_level.level,
                status=DocumentApproval.PENDING,
                created_by=self.created_by,
            )

            send_notification_email(
                user=next_level.approver,
                employee=None,
                branch=self.branch,
                title="Request Created",
                notification_type="document",
                message=(f"Your DocumentRequest {self.request_type}"
                        f"(Document No: {self.document_number}) is  waiting for you Approval."
                    ),
                template_type="request_created",
                context={
                    **get_employee_context(self.employee),
                    "doc_number": self.document_number,
                    "request_type": self.request_type.type_name,
                },
                email_template_model=DocRequestEmailTemplate,
                notification_model=DocRequestNotification,
            )

        else:

            self.status = "Approved"
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Request Approved",
                notification_type="document",
                message=(f"Your DocumentRequest {self.request_type}"
                        f"(Document No: {self.document_number}) has been fully Approved."
                    ),
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    "doc_number": self.document_number,
                    "request_type": self.request_type.type_name,
                },
                email_template_model=DocRequestEmailTemplate,
                notification_model=DocRequestNotification,
            )

class DocumentApprovalWorkflow(models.Model):
    request_type = models.ForeignKey(DocRequestType, related_name='approval_levels', on_delete=models.CASCADE, null=True, blank=True)  # Nullable for common workflow 
    branch       = models.ManyToManyField('OrganisationManager.brnch_mstr',blank=True)
    APPROVAL_TYPE_CHOICES = [
        ('no_approval', 'No Approval'),
        ('reporting_manager', 'Reporting Manager'),
        ('multi_approval', 'Multi Approval'),
    ]

    approval_type = models.CharField(
        max_length=30,
        choices=APPROVAL_TYPE_CHOICES,
        default='no_approval'
    )

class DocumentApprovalLevel(models.Model):
    workflow = models.ForeignKey(DocumentApprovalWorkflow, related_name='document_levels', on_delete=models.CASCADE, null=True)
    level = models.IntegerField()
    role = models.CharField(max_length=50, null=True, blank=True)  # Use this for role-based approval like 'CEO' or 'Manager'
    approver = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True, on_delete=models.SET_NULL)  # Use this for user-based approval


class DocumentApproval(models.Model):
    PENDING = 'Pending'
    APPROVED = 'Approved'
    REJECTED = 'Rejected'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (APPROVED, 'Approved'),
        (REJECTED, 'Rejected'),
    ]

    document_request = models.ForeignKey(DocumentRequest, related_name='doc_approvals', on_delete=models.CASCADE)
    approver        = models.ForeignKey('UserManagement.CustomUser', on_delete=models.CASCADE, null=True)
    role            = models.CharField(max_length=50, null=True, blank=True)
    level           = models.IntegerField(default=1)
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    note            = models.TextField(null=True, blank=True)
    deligate_to     = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,blank=True,related_name='docdeligations_received')
    is_deligate     = models.BooleanField(default=False)
    deligate_response = models.TextField(null=True, blank=True)
    created_at      = models.DateField(auto_now_add=True)
    created_by      = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    updated_at      = models.DateField(auto_now=True)

   
    def approve(self, note=None):
        """
        Approve current level and move workflow to next level.
        """

        self.status = self.APPROVED

        if note:
            self.note = note

        self.save(update_fields=["status", "note"])

        self.document_request.move_to_next_level()

    def reject(self, note=None):
        """
        Reject request and notify creator.
        """

        self.status = self.REJECTED

        if note:
            self.note = note

        self.save(update_fields=["status", "note"])

        self.document_request.status = "Rejected"
        self.document_request.save(update_fields=["status"])

        send_notification_email(
            user=self.document_request.created_by,
            employee=self.document_request.employee,
            message=(f"Your DocumentRequest {self.document_request.request_type}"
                     f"(Document No: {self.document_request.document_number}) has been Rejected."
                    ),
            template_type="request_rejected",
            context={
                **get_employee_context(self.document_request.employee),
                "doc_number": self.document_request.document_number,
                "request_type": self.document_request.request_type.type_name,
                "rejection_reason": self.note or "Rejected",
            },
            email_template_model=DocRequestEmailTemplate,
            notification_model=DocRequestNotification,
        )

    def __str__(self):
        return (
            f"{self.document_request.document_number} - "
            f"Level {self.level} - "
            f"{self.status}"
        )
    

@receiver(post_save, sender=DocumentRequest)
def create_initial_approval(sender, instance, created, **kwargs):

    if not created:
        return

    with transaction.atomic():
        workflow = DocumentApprovalWorkflow.objects.filter(
            request_type=instance.request_type,
            branch__in=[instance.employee.emp_branch_id]
            ).first()
        # Optional fallback like General Request
        if not workflow:
            workflow = DocumentApprovalWorkflow.objects.filter(
                request_type=instance.request_type
                ).first()
            
        approval_type = workflow.approval_type

        # -------------------------------------------------
        # NO APPROVAL
        # -------------------------------------------------
        if approval_type == "no_approval":

            if not DocumentApproval.objects.filter(
                document_request=instance,
                level=1
            ).exists():

                approver = instance.created_by

                if not approver and hasattr(instance.employee, "user"):
                    approver = instance.employee.user

                DocumentApproval.objects.create(
                    document_request=instance,
                    approver=approver,
                    role="Auto Approval",
                    level=1,
                    status=DocumentApproval.APPROVED,
                    created_by=instance.created_by
                )

            instance.status = "Approved"
            instance.save(update_fields=["status"])

            if approver:
                send_notification_email(
                    user=approver,
                    employee=instance.employee,
                    branch=instance.branch,
                    notification_type="document_request",
                    title="Document Request Approved",
                    message=(f"Your DocumentRequest {instance.request_type}"
                            f"(Document No: {instance.document_number}) has been AutoApproved."
                    ),
                    template_type="request_approved",
                    context={
                        **get_employee_context(instance.employee),
                        "doc_number": instance.document_number,
                        "request_type": instance.request_type.type_name,
                    },
                    email_template_model=DocRequestEmailTemplate,
                    notification_model=DocRequestNotification,
                )

            return

        # -------------------------------------------------
        # REPORTING MANAGER
        # -------------------------------------------------
        if approval_type == "reporting_manager":
            manager = getattr(instance.employee, "emp_reporting_manager", None)
            if not manager:
                raise Exception("Employee has no valid reporting manager.")

            DocumentApproval.objects.create(
                document_request=instance,
                approver=manager,
                role="Reporting Manager",
                status=DocumentApproval.PENDING,
                level=1,
                created_by=instance.created_by,
            )

            send_notification_email(
                user=manager,
                employee=None,
                branch=instance.branch,
                notification_type="document_request",
                title="Document Request Approval",
                message=(f"Your DocumentRequest {instance.request_type}"
                         f"(Document No: {instance.document_number}) is waiting for your Approval."
                    ),
                template_type="request_created",
                context={
                    **get_employee_context(instance.employee),
                    "doc_number": instance.document_number,
                    "request_type": instance.request_type.type_name,
                },
                email_template_model=DocRequestEmailTemplate,
                notification_model=DocRequestNotification,
                )
            return

        # -------------------------------------------------
        # MULTI APPROVAL
        # -------------------------------------------------
        if approval_type == "multi_approval":

            first_level = workflow.document_levels.order_by("level").first()

            if not first_level:
                print("No approval level configured.")
                return

            if DocumentApproval.objects.filter(
                document_request=instance,
                level=first_level.level
            ).exists():
                return

            DocumentApproval.objects.create(
                document_request=instance,
                approver=first_level.approver,
                role=first_level.role,
                level=first_level.level,
                status=DocumentApproval.PENDING,
                created_by=instance.created_by
            )


            if first_level.approver:
                send_notification_email(
                    user=first_level.approver,
                    employee=None,
                    branch=instance.branch,
                    notification_type="document_request",
                    title="Document Request Approval",
                    message=(f"Your DocumentRequest {instance.request_type}"
                            f"(Document No: {instance.document_number}) is waiting for your Approval."
                    ),
                    template_type="request_created",
                    context={
                        **get_employee_context(instance.employee),
                        "doc_number": instance.document_number,
                        "request_type": instance.request_type.type_name,
                    },
                    email_template_model=DocRequestEmailTemplate,
                    notification_model=DocRequestNotification,
                )
                return
        
class ResignationEmailTemplate(models.Model):
    template_type = models.CharField(max_length=50, choices=[
        ('resignation_created', 'Resignation Created'),
        ('resignation_approved', 'Resignation Approved'),
        ('resignation_rejected', 'Resignation Rejected')
    ])
    subject             = models.CharField(max_length=255)
    body                = models.TextField()
    created_at          = models.DateTimeField(auto_now_add=True)
    created_by          = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    branch              = models.ManyToManyField('OrganisationManager.brnch_mstr',blank=True)
    Department          = models.ManyToManyField('OrganisationManager.dept_master',blank=True)
    Category            = models.ManyToManyField('OrganisationManager.ctgry_master',blank=True)
    Designation         = models.ManyToManyField('OrganisationManager.desgntn_master',blank=True)
    def __str__(self):
        return f"{self.template_type} - {self.subject}"
    
class ResignationRequestNotification(models.Model):
    recipient_user = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True, on_delete=models.CASCADE)
    recipient_employee = models.ForeignKey(emp_master, null=True, blank=True, on_delete=models.CASCADE, related_name='resignation_notification')
    message = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    deligate_user = models.ForeignKey('UserManagement.CustomUser',null=True,blank=True,on_delete=models.CASCADE,related_name='resignation_deligated_notifications')

    def __str__(self):
        if self.recipient_user:
            return f"Notification for {self.recipient_user.emp_code}: {self.message}"
        else:
            return f"Notification for employee: {self.message}"

class EmployeeResignation(models.Model):
    TERMINATION_TYPE_CHOICES = [
        ('resignation', 'Resignation'),
        ('termination', 'Termination'),
        ('retirement', 'Retirement'),
        ('death_or_disablement', 'Death or Disablement')
    ]
    document_number  = models.CharField(max_length=50, unique=True, null=True, blank=True)
    document_date = models.DateField()
    employee = models.ForeignKey('emp_master', on_delete=models.CASCADE, related_name='resignations')
    branch           = models.ForeignKey("OrganisationManager.brnch_mstr", on_delete=models.CASCADE)
    # employee_name = models.CharField(max_length=255)
    resigned_on = models.DateField()
    notice_period = models.PositiveIntegerField(null=True, blank=True, help_text="Notice period in days")
    last_working_date = models.DateField()
    location = models.CharField(max_length=255)
    termination_type = models.CharField(max_length=20, choices=TERMINATION_TYPE_CHOICES)
    reason_for_leaving = models.TextField(blank=True, null=True)
    attachment = models.FileField(upload_to='resignation_docs/', blank=True, null=True)
    status           =  models.CharField(max_length=20, default='Pending')
    created_by      = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    class Meta:
        permissions = [
            ("view_approved_resignations", "Can view approved resignations"),
            ("add_create_eos_for_resignation", "Can add  EOS for approved resignation"),
        ]

    def __str__(self):
        return f"{self.employee} - {self.termination_type.title()} on {self.resigned_on}"
    def move_to_next_level(self):

        # ---------------- REJECT ---------------- #
        if self.resign_approvals.filter(status=ResignationApproval.REJECTED).exists():
            self.status = 'Rejected'
            self.save()

            # ⚠️ Optional: remove if already sending in reject()
            send_notification_email(
                employee=self.employee,
                branch=self.branch,
                title="Request Rejected",
                notification_type="resignation",
                message=(f"Your ResignationRequest {self.termination_type}"
                        f"(Document No: {self.document_number}) has been Rejected."),
                template_type="resignation_rejected",
                context={
                    **get_employee_context(self.employee),
                    'document_date': self.document_date,
                    'resigned_on': self.resigned_on,
                    'notice_period': self.notice_period,
                    'last_working_date': self.last_working_date,
                    'location': self.location,
                    'termination_type': self.termination_type,
                    'reason_for_leaving': self.reason_for_leaving,
                    'status': self.status,
                },
                email_template_model=ResignationEmailTemplate,
                notification_model=ResignationRequestNotification
            )
            return

        # ---------------- GET WORKFLOW ---------------- #
        workflow = ResignationApprovalWorkflow.objects.filter(
            branch__id=self.employee.emp_branch_id_id
            ).first()

        # ✅ ADD fallback (same as GeneralRequest)
        if not workflow:
            workflow = ResignationApprovalWorkflow.objects.create(
                approval_type='no_approval'
            )

            ResignationApprovalLevel.objects.create(
                workflow=workflow,
                level=1,
                role="Auto Level",
                approver=None
            )

        approval_type = workflow.approval_type

        # =========================================================
        # ✅ MINIMUM APPROVAL CHECK (ADDED)
        # =========================================================
        approved_count = self.resign_approvals.filter(
            status=ResignationApproval.APPROVED
        ).count()

        min_required = getattr(self, 'min_approvals_required', None)

        if min_required and approved_count >= min_required:
            self.status = 'Approved'
            self.save()
            send_notification_email(
                # user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Request Approved",
                notification_type="resignation",
                message=(f"Your ResignationRequest {self.termination_type}"
                        f"(Document No: {self.document_number}) has been Approved."),
                template_type="request_approved",
                context={
                     **get_employee_context(self.employee),
                    'document_date': self.document_date,
                    'resigned_on': self.resigned_on,
                    'notice_period': self.notice_period,
                    'last_working_date': self.last_working_date,
                    'location': self.location,
                    'termination_type': self.termination_type,
                    'reason_for_leaving': self.reason_for_leaving,
                    'status': self.status,
                },
                email_template_model=ResignationEmailTemplate,
                notification_model=ResignationRequestNotification,
            )
            return


        # =========================================================
        # ✅ NO APPROVAL
        # =========================================================
        if approval_type == 'no_approval':
            self.status = 'Approved'
            self.save()
            send_notification_email(
                # user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Request Approved",
                notification_type="resignation",
                message=(f"Your ResignationRequest {self.termination_type}"
                        f"(Document No: {self.document_number})has been AutoApproved."),
                template_type="request_approved",
                context={
                     **get_employee_context(self.employee),
                    'document_date': self.document_date,
                    'resigned_on': self.resigned_on,
                    'notice_period': self.notice_period,
                    'last_working_date': self.last_working_date,
                    'location': self.location,
                    'termination_type': self.termination_type,
                    'reason_for_leaving': self.reason_for_leaving,
                    'status': self.status,
                },
                email_template_model=ResignationEmailTemplate,
                notification_model=ResignationRequestNotification,
            )
            return



        # =========================================================
        # ✅ REPORTING MANAGER
        # =========================================================
        if approval_type == 'reporting_manager':

            if self.resign_approvals.filter(status=ResignationApproval.APPROVED).exists():
                self.status = 'Approved'
                self.save()
                send_notification_email(
                # user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Request Approved",
                notification_type="resignation",
                message=(f"Your ResignationRequest {self.termination_type}"
                        f"(Document No: {self.document_number}) has been Approved by ReportingManager."),
                template_type="request_approved",
                context={
                     **get_employee_context(self.employee),
                    'document_date': self.document_date,
                    'resigned_on': self.resigned_on,
                    'notice_period': self.notice_period,
                    'last_working_date': self.last_working_date,
                    'location': self.location,
                    'termination_type': self.termination_type,
                    'reason_for_leaving': self.reason_for_leaving,
                    'status': self.status,
                },
                email_template_model=ResignationEmailTemplate,
                notification_model=ResignationRequestNotification,
            )
            return



        # =========================================================
        # ✅ MULTI APPROVAL (MATCHED WITH GENERAL REQUEST)
        # =========================================================

        last_approved = self.resign_approvals.filter(
            status=ResignationApproval.APPROVED
        ).order_by('-level').first()

        # ✅ FIXED (same logic as GeneralRequest)
        current_level = (last_approved.level + 1) if last_approved else 1

        # ✅ IMPORTANT: prevent duplicate level creation (early exit)
        if self.resign_approvals.filter(level=current_level).exists():
            return

        next_level = workflow.resignation_levels.filter(
            level=current_level
        ).first()

        if next_level and next_level.approver:

            last_approval = self.resign_approvals.order_by('-level').first()

            ResignationApproval.objects.create(
                resignation_request=self,
                approver=next_level.approver,
                role=next_level.role,
                level=next_level.level,
                status=ResignationApproval.PENDING,
                note=last_approval.note if last_approval else None
            )
            send_notification_email(
                            employee=self.employee,
                            branch=self.branch,
                            title="Request Created",
                            notification_type="resignation",
                            message=(f"Your ResignationRequest {self.termination_type}"
                                    f"(Document No: {self.document_number})waiting for your Approval."),
                            template_type="resignation_created",
                            context={
                                **get_employee_context(self.employee),
                                'document_date': self.document_date,
                                'resigned_on': self.resigned_on,
                                'notice_period': self.notice_period,
                                'last_working_date': self.last_working_date,
                                'location': self.location,
                                'termination_type': self.termination_type,
                                'reason_for_leaving': self.reason_for_leaving,
                                'status': self.status,
                            },
                            email_template_model=ResignationEmailTemplate,
                            notification_model=ResignationRequestNotification
                        )


        else:
            self.status = 'Approved'
            self.save()

            send_notification_email(
                employee=self.employee,
                branch=self.branch,
                title="Request Approved",
                notification_type="resignation",
                message=(f"Your ResignationRequest {self.termination_type}"
                        f"(Document No: {self.document_number}) has been fully Approved."),
                template_type="resignation_approved",
                context={
                    **get_employee_context(self.employee),
                    'document_date': self.document_date,
                    'resigned_on': self.resigned_on,
                    'notice_period': self.notice_period,
                    'last_working_date': self.last_working_date,
                    'location': self.location,
                    'termination_type': self.termination_type,
                    'reason_for_leaving': self.reason_for_leaving,
                    'status': self.status,
                },
                email_template_model=ResignationEmailTemplate,
                notification_model=ResignationRequestNotification
            )
            return
class ResignationApprovalWorkflow(models.Model):
     APPROVAL_TYPE_CHOICES = [
        ('no_approval', 'No Approval'),
        ('reporting_manager', 'Reporting Manager'),
        ('multi_approval', 'Multi Approval'),
    ]
     
     branch= models.ManyToManyField('OrganisationManager.brnch_mstr', blank=True)
     approval_type = models.CharField(
        max_length=30,
        choices=APPROVAL_TYPE_CHOICES,
        default='no_approval'
    )

class ResignationApprovalLevel(models.Model):
    workflow = models.ForeignKey(ResignationApprovalWorkflow,related_name='resignation_levels',on_delete=models.CASCADE,null=True)
    level = models.PositiveIntegerField()
    approver = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True)
    role = models.CharField(max_length=100,blank=True,null=True)
    

    class Meta:
        ordering = ['level']

    def __str__(self):
        return f"Level {self.level} - {self.role} ({self.approver})"
    
class ResignationApproval(models.Model):
    PENDING = 'Pending'
    APPROVED = 'Approved'
    REJECTED = 'Rejected'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (APPROVED, 'Approved'),
        (REJECTED, 'Rejected'),
    ]
    resignation_request = models.ForeignKey(EmployeeResignation, related_name='resign_approvals', on_delete=models.CASCADE)
    approver        = models.ForeignKey('UserManagement.CustomUser', on_delete=models.CASCADE,null=True)
    role            = models.CharField(max_length=50, null=True, blank=True)
    level           = models.IntegerField(default=1)
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES,default=PENDING)
    note            = models.TextField(null=True, blank=True)
    deligate_to     = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,blank=True,related_name='resignation_deligations_received')
    is_deligate     = models.BooleanField(default=False)
    deligate_response = models.TextField(null=True, blank=True)
    created_at      = models.DateField(auto_now_add=True)
    created_by      = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    updated_at      = models.DateField(auto_now=True)
   
    def approve(self,note=None):
        self.status = self.APPROVED
        if note:
            self.note = note
        self.save()
        self.resignation_request.move_to_next_level()
    def reject(self,note=None):
        self.status = self.REJECTED
        if note:
            self.note = note
        self.save()
        self.resignation_request.status = 'Rejected'
        self.resignation_request.save()
        send_notification_email(
                user=self.created_by,
                employee=self.resignation_request.employee,
                branch=self.resignation_request.branch,
                title="Request Rejected",
                notification_type="resignation",
                message=(f"Your ResignationRequest {self.resignation_request}"
                 f"(Document No: {self.resignation_request.document_number}) has been Rejected."),
                template_type="resignation_rejected",
                context={
                    **get_employee_context(self.resignation_request.employee),
                    'document_date':self.resignation_request.document_date,
                    'resigned_on':self.resignation_request.resigned_on,
                    'notice_period':self.resignation_request.notice_period,
                    'last_working_date':self.resignation_request.last_working_date,
                    'location':self.resignation_request.location,
                    'termination_type':self.resignation_request.termination_type,
                    'reason_for_leaving':self.resignation_request.reason_for_leaving,
                    'status':self.status,
                },
                    email_template_model=ResignationEmailTemplate,
                    notification_model=ResignationRequestNotification
                )


@receiver(post_save, sender=EmployeeResignation)
def create_initial_approval(sender, instance, created, **kwargs):

    if not created:
        return

    # ✅ FIX 1: Get workflow based on employee branch
    workflow = ResignationApprovalWorkflow.objects.filter(
       branch=instance.employee.emp_branch_id 
    ).first()

    if not workflow:
        return

    approval_type = workflow.approval_type

    # ---------------- NO APPROVAL ----------------
    if approval_type == 'no_approval':

        # ✅ Safe approver fallback
        approver = instance.created_by or getattr(instance.employee, 'emp_reporting_manager', None)

        # ✅ Dynamic role (optional but better)
        if approver:
            role = getattr(approver, 'designation', None) or "Auto Approval"
        else:
            role = "System Auto Approval"

        # ✅ Create approval (even if approver is None, if allowed)
        ResignationApproval.objects.create(
            resignation_request=instance,
            approver=approver,
            role=role,
            level=1,
            status=ResignationApproval.APPROVED
        )

        # ✅ Always update status (no failure)
        instance.status = "Approved"
        instance.save(update_fields=["status"])

        # ✅ Send notification safely
        send_notification_email(
            user=approver,  # can be None, your function should handle it
            employee=instance.employee,
            branch=instance.employee.emp_branch_id,
            title="Request Approved",
            notification_type="resignation",
            message=(f"Your ResignationRequest {instance.termination_type}"
                    f"(Document No: {instance.document_number})has been AutoApproved."),
            template_type="resignation_approved",
            context={
                **get_employee_context(instance.employee),
                'document_date': instance.document_date,
                'termination_type': instance.termination_type,
                'status': instance.status,
            },
            email_template_model=ResignationEmailTemplate,
            notification_model=ResignationRequestNotification
        )

        return


    # ---------------- REPORTING MANAGER ----------------
    if approval_type == 'reporting_manager':

        manager = instance.employee.emp_reporting_manager

        if not manager:
            raise Exception("Employee has no reporting manager. Please set reporting manager.")

        ResignationApproval.objects.create(
            resignation_request=instance,
            approver=manager,
            role="Reporting Manager",
            level=1,
            status=ResignationApproval.PENDING
        )

        send_notification_email(
            user=manager,
            employee=instance.employee,
            branch=instance.employee.emp_branch_id,
            title="Request Created",
            notification_type="resigantion",
            message=(f"New ResignationRequest {instance.termination_type}"
                    f"(Document No: {instance.document_number}) is waiting for your Approval"),
            template_type="resignation_created",
            context={
                **get_employee_context(instance.employee),
                'document_date': instance.document_date,
                'termination_type': instance.termination_type,
            },
            email_template_model=ResignationEmailTemplate,
            notification_model=ResignationRequestNotification
        )

        return


    # ---------------- MULTI APPROVAL ----------------
    if approval_type == 'multi_approval':

        first_level = workflow.resignation_levels.first()  # uses Meta ordering

        if not first_level:
            return

        if not first_level.approver:
            raise Exception(f"No approver set for level {first_level.level}")

        ResignationApproval.objects.create(
            resignation_request=instance,
            approver=first_level.approver,
            role=first_level.role,
            level=first_level.level,
            status=ResignationApproval.PENDING
        )

        send_notification_email(
            user=first_level.approver,
            employee=instance.employee,
            branch=instance.employee.emp_branch_id,
            title="Request Created",
            notification_type="resignation",
            message=(f"Your ResignationRequest {instance.termination_type}"
                    f"(Document No: {instance.document_number}) is waiting for your Approval"),
            template_type="resignation_created",
            context={
                **get_employee_context(instance.employee),
                'document_date': instance.document_date,
                'termination_type': instance.termination_type,
            },
            email_template_model=ResignationEmailTemplate,
            notification_model=ResignationRequestNotification
        )
        return
        
    
class EndOfService(models.Model):
    resignation = models.OneToOneField(EmployeeResignation, on_delete=models.CASCADE, related_name='eos')
    years_of_service = models.FloatField(help_text="Years of service calculated")
    date_of_joining = models.DateField()
    date_of_resignation_termination = models.DateField()
    last_working_date = models.DateField()
    notice_period_days = models.PositiveIntegerField(default=0)
    total_service_days = models.PositiveIntegerField(default=0)
    net_number_of_days_worked = models.FloatField(default=0)
    leave_days_without_pay = models.FloatField(default=0)
    leave_balance = models.FloatField(default=0)
    last_month_salary = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    gratuity_days = models.FloatField(default=0)
    gratuity_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    notice_pay = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    final_month_salary = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    # leave_salary = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    air_ticket = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    processed_date = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=[
        ('pending', 'Pending'),
        ('processed', 'Processed'),
        ('paid', 'Paid')
    ], default='pending')


    def __str__(self):
        return f"EOS for {self.resignation} - {self.gratuity_amount} AED"