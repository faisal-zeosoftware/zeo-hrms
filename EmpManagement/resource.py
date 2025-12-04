from import_export import resources,fields,widgets
import phonenumbers
from phonenumbers import NumberParseException
from datetime import timedelta,timezone
from datetime import datetime,date
from .models import (emp_master, Emp_CustomField,notification,Emp_Documents,LanguageSkill,MarketingSkill,ProgrammingLanguageSkill,Emp_CustomFieldValue,EmpDocuments_CustomField,
                     Doc_CustomFieldValue,EmployeeBankDetail)
from import_export.widgets import DateWidget
from datetime import datetime
from import_export.widgets import Widget
from django.core.exceptions import ValidationError
from django.db import models
import re
from Core.models import document_type,state_mstr,cntry_mstr,Nationality,ReligionMaster
from OrganisationManager.models import brnch_mstr,ctgry_master,dept_master,desgntn_master
from import_export.widgets import ForeignKeyWidget
from django.core.files.base import ContentFile
import os
from django.core.files.storage import default_storage
from .models import NotificationSettings
from .tasks import send_document_notification

class CaseInsensitiveForeignKeyWidget(ForeignKeyWidget):
    def clean(self, value, row=None, *args, **kwargs):
        if not value:
            return None
        return self.model.objects.filter(**{f"{self.field}__iexact": value.strip()}).first()


class FileWidget(Widget):
    def clean(self, value, row=None, *args, **kwargs):
        """
        Handles importing file names and linking them to the ImageField.
        """
        if value:
            # Build the file path relative to MEDIA_ROOT
            file_path = os.path.join('emp_profile_pic', value)  # Folder and file name

            # Check if the file exists in the storage
            if default_storage.exists(file_path):
                # If file exists, open and return as ContentFile
                with default_storage.open(file_path, 'rb') as file:
                    return ContentFile(file.read(), name=value)
            else:
                # If file doesn't exist, handle accordingly (e.g., skip or raise error)
                return None
        return None

    def render(self, value, obj=None):
        """
        Handles exporting file paths for file fields.
        """
        if value and hasattr(value, "url"):
            return value.url  # Return the file's URL
        return ""

class NumericMobileNumberWidget(Widget):   
    def clean(self, value, row=None, *args, **kwargs):
        # Clean the value - convert it to an integer.    
        if value:
            try:
                return int(value)
            except ValueError:
                raise ValidationError("Mobile number must be numeric.")
        return None
class CustomForeignKeyWidget(ForeignKeyWidget):
    def clean(self, value, row=None, *args, **kwargs):
        if not value:
            return None

        value = value.strip()  # remove spaces

        # Get branch name from Excel
        branch_name = row.get('Employee Branch Code')

        matching_branch = brnch_mstr.objects.filter(branch_name__iexact=branch_name.strip()).first()
        if not matching_branch:
            raise ValidationError(f"No matching branch found for Branch Name: {branch_name}")

        # CASE-INSENSITIVE department lookup
        queryset = self.get_queryset(value, row, *args, **kwargs)
        queryset = queryset.filter(
            branch_id=matching_branch.id,
            dept_name__iexact=value
        )

        if queryset.count() == 1:
            return queryset.first()
        elif queryset.count() > 1:
            raise ValidationError(
                f"Multiple departments found for '{value}' in branch '{branch_name}'"
            )
        else:
            raise ValidationError(
                f"No department found for '{value}' in branch '{branch_name}'"
            )
        
# Custom Date Widget to handle the date format
class MultiTypeWidget(Widget):
    def clean(self, value, row=None, *args, **kwargs):
        if value:
            try:
                # Attempt to parse date in the format 'YYYY-MM-DD'
                return datetime.strptime(value, '%d-%m-%y').date()
            except ValueError:
                try:
                    # Attempt to parse date in the format 'YYYY-MM-DD HH:MM:SS'
                    return datetime.strptime(value, '%d-%m-%y %H:%M:%S').date()
                except ValueError:
                    # Return as string if it's not a date
                    return value
        return None

    def render(self, value, obj=None):
        if isinstance(value, datetime):
            return value.strftime('%d-%m-%y')
        return str(value)

class CustomDateWidget(Widget):
    """Handles datetime, timedelta, and string date formats for Excel and CSV"""

    def clean(self, value, row=None, *args, **kwargs):
        if value in (None, ''):
            return None
        # Excel returns timedelta sometimes
        if isinstance(value, timedelta):
            excel_start_date = datetime(1899, 12, 30)
            return (excel_start_date + value).date()
        # Excel returns datetime
        if isinstance(value, datetime):
            return value.date()
        # CSV string
        value = str(value).strip()
        for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%d/%m/%y', '%d-%m-%y'):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Invalid date format: {value}")

    def render(self, value, obj=None):
        if not value:
            return ''
        return value.strftime('%d/%m/%Y')
class EmployeeResource(resources.ModelResource):
    emp_code = fields.Field(attribute='emp_code', column_name='Employee Code')
    emp_first_name = fields.Field(attribute='emp_first_name', column_name='Employee First Name')
    emp_last_name = fields.Field(attribute='emp_last_name', column_name='Employee Last Name')
    emp_gender = fields.Field(attribute='emp_gender', column_name='Employee Gender')
    emp_date_of_birth = fields.Field(attribute='emp_date_of_birth', column_name='Employee DOB(DD/MM/YYYY)', widget=CustomDateWidget())
    emp_personal_email = fields.Field(attribute='emp_personal_email', column_name='Employee Personal Email ID')
    emp_company_email= fields.Field(attribute='emp_company_email', column_name='Employee Company Email ID')
    is_ess = fields.Field(attribute='is_ess', column_name='Iss ESS (True/False)')
    emp_mobile_number_1 = fields.Field(attribute='emp_mobile_number_1', column_name='Employee Personal Mob No')
    emp_mobile_number_2 = fields.Field(attribute='emp_mobile_number_2', column_name='Employee Company Mobile No')
    emp_country_id = fields.Field(attribute='emp_country_id', column_name='Employee Country Code',widget=CaseInsensitiveForeignKeyWidget(cntry_mstr, 'country_name'))
    emp_state_id = fields.Field(attribute='emp_state_id', column_name='Employee State',widget=CaseInsensitiveForeignKeyWidget(state_mstr, 'state_name'))
    emp_city = fields.Field(attribute='emp_city', column_name='Employee City')
    emp_permenent_address = fields.Field(attribute='emp_permenent_address', column_name='Employee Permanent Address')
    emp_present_address = fields.Field(attribute='emp_present_address', column_name='Employee Current Address')
    emp_status = fields.Field(attribute='emp_status', column_name='Employee Status(True/False)')
    emp_joined_date = fields.Field(attribute='emp_joined_date', column_name='Employee Joining Date(DD/MM/YYYY)', widget=CustomDateWidget())
    emp_date_of_confirmation = fields.Field(attribute='emp_date_of_confirmation', column_name='Employee Confirmaton Date(DD/MM/YYYY)', widget=CustomDateWidget())
    emp_relegion = fields.Field(attribute='emp_relegion', column_name='Employee Religion', widget=ForeignKeyWidget(ReligionMaster, 'religion'))
    emp_blood_group = fields.Field(attribute='emp_blood_group', column_name='Employee Blood Group')
    emp_nationality = fields.Field(attribute='emp_nationality', column_name='Employee Nationality', widget=CaseInsensitiveForeignKeyWidget(Nationality, 'N_name'))
    emp_marital_status = fields.Field(attribute='emp_marital_status', column_name='Employee Marital Status')
    emp_father_name = fields.Field(attribute='emp_father_name', column_name='Employee Father Name')
    emp_mother_name = fields.Field(attribute='emp_mother_name', column_name='Employee Mother Name')
    is_active = fields.Field(attribute='is_active', column_name='Employee Active(True/False)')
    emp_ot_applicable = fields.Field(attribute='emp_ot_applicable', column_name='Employee OT applicable(True/False)')
    emp_branch_id = fields.Field(attribute='emp_branch_id', column_name='Employee Branch Code', widget=ForeignKeyWidget(brnch_mstr, 'branch_name'))
    emp_dept_id = fields.Field(attribute='emp_dept_id', column_name='Employee Department Code', widget=CustomForeignKeyWidget(dept_master, 'dept_name'))
    emp_desgntn_id = fields.Field(attribute='emp_desgntn_id', column_name='Employee Designation Code', widget=CaseInsensitiveForeignKeyWidget(desgntn_master, 'desgntn_job_title'))
    emp_ctgry_id = fields.Field(attribute='emp_ctgry_id', column_name='Employee Category Code', widget=ForeignKeyWidget(ctgry_master, 'ctgry_title'))
    person_id = fields.Field(attribute='person_id', column_name='Person ID')
    work_location = fields.Field(attribute='work_locatio', column_name='Employee Work Location', widget=ForeignKeyWidget(brnch_mstr, 'branch_name'))
    visa_location = fields.Field(attribute='visa_location', column_name='Employee Visa Location', widget=ForeignKeyWidget(brnch_mstr, 'branch_name'))
    emp_profile_pic = fields.Field(attribute='emp_profile_pic', column_name='Employee Profile Picture', widget=FileWidget())

    class Meta:
        model = emp_master     
        fields = (
            'emp_code','emp_first_name','emp_last_name','emp_gender','emp_date_of_birth',
            'emp_personal_email','emp_company_email','is_ess','emp_mobile_number_1',
            'emp_mobile_number_2','emp_country_id','emp_state_id','emp_city',
            'emp_permenent_address','emp_present_address','emp_status','emp_joined_date',
            'emp_date_of_confirmation','emp_relegion','emp_blood_group','emp_nationality',
            'emp_marital_status','emp_father_name','emp_mother_name',
            'is_active','emp_ot_applicable','emp_branch_id','emp_dept_id','emp_desgntn_id',
            'emp_ctgry_id','emp_profile_pic','person_id','work_location','visa_location'
        )
        import_id_fields = ['emp_code'] 
        skip_unchanged = True
        report_skipped = True
    def before_import_row(self, row, **kwargs):
        if isinstance(row, list):
            row = dict(zip(self.get_header_names(), row))

        errors = []

        # 1️⃣ Normalize strings and replace None
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = " ".join(value.split())
            elif value is None:
                row[key] = ""
        mandatory_fields = {
        'Employee DOB(DD/MM/YYYY)': 'Employee Date of Birth',
        'Employee Joining Date(DD/MM/YYYY)': 'Employee Joining Date'
        }
        for field, display_name in mandatory_fields.items():
            if not row.get(field):
                errors.append(f"{display_name} is mandatory and cannot be empty.")

        # If mandatory errors found, raise immediately
        if errors:
            raise ValidationError(errors)
        # 2️⃣ Branch
        branch_name = row.get('Employee Branch Code', '').strip()
        department_name = row.get('Employee Department Code', '').strip()
        designation_name = row.get('Employee Designation Code', '').strip()
        category_name = row.get('Employee Category Code', '').strip()
        work_location_name = row.get('Employee Work Location', '').strip()
        visa_location_name = row.get('Employee Visa Location', '').strip()

        matching_branch = None
        if branch_name:
            matching_branch = brnch_mstr.objects.filter(branch_name__iexact=branch_name).first()
            if not matching_branch:
                errors.append(f"No matching branch found for Branch: '{branch_name}'")
            else:
                row['emp_branch_id'] = matching_branch.id
        else:
            errors.append("Employee Branch Code is required.")

        # 3️⃣ Department (check branch dependency)
        if matching_branch and department_name:
            dept = dept_master.objects.filter(
                branch_id=matching_branch.id,
                dept_name__iexact=department_name
            ).first()
            if not dept:
                errors.append(f"No matching Department '{department_name}' found in Branch '{branch_name}'")
            else:
                row['emp_dept_id'] = dept.id
        elif department_name:
            errors.append(f"Department '{department_name}' ignored because Branch not found.")
        else:
            row['emp_dept_id'] = None

        # 4️⃣ Designation
        if designation_name:
            desg = desgntn_master.objects.filter(desgntn_job_title__iexact=designation_name).first()
            if not desg:
                errors.append(f"No matching Designation found for '{designation_name}'")
            else:
                row['emp_desgntn_id'] = desg.id
        else:
            row['emp_desgntn_id'] = None

        # 5️⃣ Category
        if category_name:
            cat = ctgry_master.objects.filter(ctgry_title__iexact=category_name).first()
            if not cat:
                errors.append(f"No matching Category found for '{category_name}'")
            else:
                row['emp_ctgry_id'] = cat.id
        else:
            row['emp_ctgry_id'] = None

        # 6️⃣ Work Location
        if work_location_name:
            work_loc = brnch_mstr.objects.filter(branch_name__iexact=work_location_name).first()
            if not work_loc:
                errors.append(f"No matching Work Location found for '{work_location_name}'")
            else:
                row['work_location'] = work_loc.id
        else:
            row['work_location'] = None

        # 7️⃣ Visa Location
        if visa_location_name:
            visa_loc = brnch_mstr.objects.filter(branch_name__iexact=visa_location_name).first()
            if not visa_loc:
                errors.append(f"No matching Visa Location found for '{visa_location_name}'")
            else:
                row['visa_location'] = visa_loc.id
        else:
            row['visa_location'] = None

        # 8️⃣ Country, State Validation
        country_name = row.get('Employee Country Code', '').strip()
        state_name = row.get('Employee State', '').strip()

        if country_name:
            country = cntry_mstr.objects.filter(country_name__iexact=country_name).first()
            if not country:
                errors.append(f"No matching Country found for '{country_name}'")
            else:
                row['emp_country_id'] = country.id
        else:
            row['emp_country_id'] = None

        if state_name:
            state = state_mstr.objects.filter(state_name__iexact=state_name).first()
            if not state:
                errors.append(f"No matching State found for '{state_name}'")
            else:
                row['emp_state_id'] = state.id
        else:
            row['emp_state_id'] = None

        # 9️⃣ Nationality
        nationality_name = row.get('Employee Nationality', '').strip()
        if nationality_name:
            nationality = Nationality.objects.filter(N_name__iexact=nationality_name).first()
            if not nationality:
                errors.append(f"No matching Nationality found for '{nationality_name}'")
            else:
                row['emp_nationality'] = nationality.id
        else:
            row['emp_nationality'] = None

        # 🔟 Religion
        religion_name = row.get('Employee Religion', '').strip()
        if religion_name:
            religion = ReligionMaster.objects.filter(religion__iexact=religion_name).first()
            if not religion:
                errors.append(f"No matching Religion found for '{religion_name}'")
            else:
                row['emp_relegion'] = religion.id
        else:
            row['emp_relegion'] = None

        # 1️⃣1️⃣ Boolean Fields (handles TRUE, true, False, etc.)
        bool_fields = {
            'Iss ESS (True/False)': 'is_ess',
            'Employee Status(True/False)': 'emp_status',
            'Employee Active(True/False)': 'is_active',
            'Employee OT applicable(True/False)': 'emp_ot_applicable'
        }
        for field, attr in bool_fields.items():
            value = row.get(field)
            if value is None or value == "":
                row[attr] = False
                continue
            if isinstance(value, bool):
                row[attr] = value
                continue
            val_str = str(value).strip().lower()
            if val_str in ['true', '1', 'yes', 'y']:
                row[attr] = True
            elif val_str in ['false', '0', 'no', 'n']:
                row[attr] = False
            else:
                errors.append(f"Invalid boolean value for {field}: '{value}'")

        # 1️⃣2️⃣ Validate Gender
        gender = row.get('Employee Gender')
        if gender and gender not in ['Male', 'Female', 'Other', 'M', 'F', 'O']:
            errors.append(f"Invalid Gender '{gender}'. Allowed: Male, Female, Other, M, F, O")

        # 1️⃣3️⃣ Validate Email
        email = row.get('Employee Personal Email ID')
        if email and not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
            errors.append(f"Invalid email format for Personal Email ID: '{email}'")
        compny_email= row.get('Employee Company Email ID')
        if compny_email and not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', compny_email):
            errors.append(f"Invalid email format For Company Email IDS: '{compny_email}'")
        
        # 1️⃣4️⃣ Validate Marital Status
        marital_status = row.get('Employee Marital Status')
        if marital_status and marital_status.lower() not in ['married', 'single', 'divorced', 'widow']:
            errors.append("Invalid Marital Status. Allowed: Married, Single, Divorced, Widow")

        # 1️⃣5️⃣ Person ID Validation
        person_id = row.get('Person ID')
        if person_id:
            person_id = str(person_id).strip()
            try:
                if 'e' in person_id.lower():
                    person_id = str(int(float(person_id)))
            except Exception:
                errors.append(f"Invalid Person ID format: '{person_id}'")

            if not re.fullmatch(r'^\d{14}$', person_id):
                errors.append(f"Invalid Person ID '{person_id}'. Must be exactly 14 digits.")
            else:
                row['person_id'] = person_id
                # if emp_master.objects.filter(person_id=person_id).exists():
                #     errors.append(f"Person ID '{person_id}' already exists. Must be unique.")
        else:
            row['person_id'] = None
        #date validation
        date_fields = ['Employee DOB(DD/MM/YYYY)', 'Employee Joining Date(DD/MM/YYYY)', 'Employee Confirmaton Date(DD/MM/YYYY)']
        for field in date_fields:
            date_value = row.get(field)
            if date_value:
                try:
                    if isinstance(date_value, datetime):
                        row[field] = date_value.strftime('%d/%m/%Y')
                    elif isinstance(date_value, timedelta):
                        excel_start_date = datetime(1899, 12, 30)
                        row[field] = (excel_start_date + date_value).strftime('%d/%m/%Y')
                    else:
                        parsed_date = None
                        for fmt in ('%d/%m/%Y','%d-%m-%Y','%d/%m/%y','%d-%m-%y'):
                            try:
                                parsed_date = datetime.strptime(date_value, fmt)
                                break
                            except ValueError:
                                continue
                        if not parsed_date:
                            errors.append(f"Invalid date format for {field}. Expected dd/mm/yyyy")
                        else:
                            row[field] = parsed_date.strftime('%d/%m/%Y')
                except Exception as e:
                    errors.append(f"Error parsing date for {field}: {str(e)}")
        # ✅ If any errors collected, stop import for this row
        if errors:
            raise ValidationError(errors)

class EmpCustomFieldValueResource(resources.ModelResource):
    emp_master = fields.Field(
        attribute='emp_master',
        column_name='Employee Code',
        widget=ForeignKeyWidget(emp_master, 'emp_code')
    )

    emp_custom_field = fields.Field(
        attribute='emp_custom_field',
        column_name='Field Name',
        widget=ForeignKeyWidget(Emp_CustomField, 'emp_custom_field')
    )

    field_value = fields.Field(
        attribute='field_value',
        column_name='Field Value'
    )

    class Meta:
        model = Emp_CustomFieldValue
        fields = ('emp_master', 'emp_custom_field', 'field_value')
        import_id_fields = ['emp_master', 'emp_custom_field']  # ensures uniqueness per employee + field
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, row_idx=None, **kwargs):
        emp_code = row.get('Employee Code', '').strip()
        field_name = row.get('Field Name', '').strip()
        field_value = row.get('Field Value', '')

        if not emp_code:
            raise ValidationError(f"Row {row_idx}: Employee Code cannot be empty.")
        if not field_name:
            raise ValidationError(f"Row {row_idx}: Field Name cannot be empty.")

        # Validate custom field exists
        try:
            custom_field = Emp_CustomField.objects.get(emp_custom_field=field_name)
        except Emp_CustomField.DoesNotExist:
            raise ValidationError(f"Row {row_idx}: Custom field '{field_name}' does not exist.")

        # Normalize date fields
        if custom_field.data_type == 'date' and field_value:
            if isinstance(field_value, (datetime, date)):
                field_value = field_value.strftime('%d-%m-%Y')
            elif isinstance(field_value, str):
                field_value = field_value.strip()
                if ' ' in field_value:
                    field_value = field_value.split(' ')[0]  # Remove time
                for fmt in ('%d-%m-%Y', '%d/%m/%Y', '%Y-%m-%d'):
                    try:
                        parsed_date = datetime.strptime(field_value, fmt)
                        field_value = parsed_date.strftime('%d-%m-%Y')
                        break
                    except ValueError:
                        continue
                else:
                    raise ValidationError(
                        f"Row {row_idx}: Invalid date format for field '{field_name}'. Expected DD-MM-YYYY or DD/MM/YYYY."
                    )

        # Validate dropdown, radio, checkbox
        elif custom_field.data_type == 'dropdown' and field_value:
            if custom_field.dropdown_values and field_value not in custom_field.dropdown_values:
                raise ValidationError(
                    f"Row {row_idx}: Value '{field_value}' not in dropdown options for '{field_name}'."
                )
        elif custom_field.data_type == 'radio' and field_value:
            if custom_field.radio_values and field_value not in custom_field.radio_values:
                raise ValidationError(
                    f"Row {row_idx}: Value '{field_value}' not in radio options for '{field_name}'."
                )
        elif custom_field.data_type == 'checkbox' and field_value:
            if custom_field.checkbox_values and field_value not in custom_field.checkbox_values:
                raise ValidationError(
                    f"Row {row_idx}: Value '{field_value}' not in checkbox options for '{field_name}'."
                )

        # Update the row with normalized value
        row['Field Value'] = field_value

        # --- Handle existing value: if already exists, update manually ---
        try:
            existing = Emp_CustomFieldValue.objects.get(
                emp_master__emp_code=emp_code,
                emp_custom_field=field_name
            )
            existing.field_value = field_value
            existing.save()
            # Mark the row as skipped so import_export does not create a duplicate
            row['skip_row'] = True
        except Emp_CustomFieldValue.DoesNotExist:
            pass

class DocumentResource(resources.ModelResource):
    emp_id = fields.Field(attribute='emp_id', column_name='Employee Code', widget=ForeignKeyWidget(emp_master, 'emp_code'))
    document_type = fields.Field(attribute='document_type', column_name='Document Type', widget=ForeignKeyWidget(document_type, 'type_name'))
    emp_doc_number = fields.Field(attribute='emp_doc_number', column_name='Document Number')
    emp_doc_issued_date = fields.Field(attribute='emp_doc_issued_date', column_name='Document Issued Date',widget=CustomDateWidget())
    emp_doc_expiry_date = fields.Field(attribute='emp_doc_expiry_date', column_name='Document Expiry Date',widget=CustomDateWidget())
    is_active = fields.Field(attribute='is_active', column_name='Active')

    class Meta:
        model = Emp_Documents
        fields = (
            'emp_id',
            'document_type',
            'emp_doc_number',
            'emp_doc_issued_date',
            'emp_doc_expiry_date',
            'is_active',
        )
        import_id_fields = ["emp_doc_number"]

    def before_import_row(self, row, **kwargs):
        if isinstance(row, list):
            row = dict(zip(self.get_header_names(), row))

        errors = []

        # 1️⃣ Normalize strings and replace None
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = " ".join(value.split())
            elif value is None:
                row[key] = ""
          
        emp_code = row.get('Employee Code')
        doc_type = row.get('Document Type')

        # Validate emp_id and document_type
        # if Emp_Documents.objects.filter(emp_sl_no=emp_sl_no).exists():
        #     errors.append(f"Duplicate value found for Employee Code: {emp_sl_no}")

        if not emp_master.objects.filter(emp_code=emp_code).exists():
            errors.append(f"emp_master matching query does not exist for ID: {emp_code}")

        if not document_type.objects.filter(type_name=doc_type).exists():
            errors.append(f"Document_type matching query does not exist for ID: {doc_type}")

        # Validate and convert date fields format
        date_fields = ['Document Issued Date', 'Document Expiry Date']
        for field in date_fields:
            date_value = row.get(field)
            if date_value:
                try:
                    if isinstance(date_value, datetime):
                        row[field] = date_value.strftime('%d/%m/%Y')
                    elif isinstance(date_value, timedelta):
                        excel_start_date = datetime(1899, 12, 30)
                        row[field] = (excel_start_date + date_value).strftime('%d/%m/%Y')
                    else:
                        parsed_date = None
                        for fmt in ('%d/%m/%Y','%d-%m-%Y','%d/%m/%y','%d-%m-%y'):
                            try:
                                parsed_date = datetime.strptime(date_value, fmt)
                                break
                            except ValueError:
                                continue
                        if not parsed_date:
                            errors.append(f"Invalid date format for {field}. Expected dd/mm/yyyy")
                        else:
                            row[field] = parsed_date.strftime('%d/%m/%Y')
                except Exception as e:
                    errors.append(f"Error parsing date for {field}: {str(e)}")
            # else:
            #     errors.append(f"Date value for {field} is empty")
        # 7️⃣ Boolean Fields
        # 4️⃣ Normalize Boolean fields
        bool_fields = {
            'Active': 'is_active',
        }

        for field, attr in bool_fields.items():
            value = row.get(field)

            # Convert typical string inputs
            if isinstance(value, str):
                value = value.strip().lower()
                if value in ['true', '1', 'yes', 'y', 't']:
                    bool_val = True
                elif value in ['false', '0', 'no', 'n', 'f', '']:
                    bool_val = False
                else:
                    errors.append(f"Invalid boolean value for {field}: {row.get(field)}")
                    bool_val = False
            elif isinstance(value, (int, bool)):
                bool_val = bool(value)
            elif value is None:
                bool_val = False
            else:
                errors.append(f"Invalid type for boolean field {field}: {type(value)}")
                bool_val = False

            # Update both field and attribute
            row[field] = bool_val
            row[attr] = bool_val

        if errors:
            raise ValidationError(errors)

    def after_import_instance(self, instance, new, **kwargs):
        """Check expiry after importing each document instance."""
        today = timezone.now().date()
        expiry_date = instance.emp_doc_expiry_date

        try:
            branch = instance.emp_id.emp_branch_id
            notification_settings = NotificationSettings.objects.get(branch=branch)
            days_before_expiry = notification_settings.days_before_expiry
        except NotificationSettings.DoesNotExist:
            days_before_expiry = 7  # Default reminder 7 days before expiry

        days_until_expiry = (expiry_date - today).days

        # Check document expiry and send notifications
        if expiry_date <= today:
            send_document_notification(instance, expiry_date, 'expired or expiring today')

        elif days_until_expiry <= days_before_expiry:
            send_document_notification(instance, expiry_date, f"expiring in {days_until_expiry} days")    

class EmpDocumentCustomFieldValueResource(resources.ModelResource):
    emp_documents = fields.Field(attribute='emp_documents',column_name='Document Number',widget=ForeignKeyWidget(Emp_Documents, 'emp_doc_number'))
    emp_custom_field = fields.Field(attribute='emp_custom_field',column_name='Field Name',widget=ForeignKeyWidget(EmpDocuments_CustomField, 'emp_custom_field'))
    field_value = fields.Field(attribute='field_value',column_name='Field Value',widget=MultiTypeWidget())

    class Meta:
        model = Doc_CustomFieldValue
        fields = ('emp_documents', 'emp_custom_field', 'field_value')
        import_id_fields = ()

    def before_import_row(self, row, row_idx=None, **kwargs):
        emp_documents = row.get('Document Number')
        field_name = row.get('Field Name' '').strip()
        field_value = row.get('Field Value')
        
        if not EmpDocuments_CustomField.objects.filter(emp_custom_field=field_name).exists():
            raise ValidationError(f"Emp_Document_CustomField with field_name {field_name} does not exist.")
        
        # if not emp_master.objects.filter(emp_code=emp_code).exists():
        #     raise ValidationError(f"emp_master with emp_code {emp_code} does not exist.")
       
        custom_field = EmpDocuments_CustomField.objects.get(emp_custom_field=field_name)

        if custom_field.data_type == 'date':
            if isinstance(field_value, str):
                field_value = field_value.strip()  # Remove leading and trailing spaces
                
                # Check if the string contains time information
                if ' ' in field_value:
                    # Extract the date part (YYYY-MM-DD) from datetime string
                    field_value = field_value.split(' ')[0]
                
                try:
                    # Attempt to parse the date from the extracted or provided string
                    date_object = datetime.strptime(field_value, '%Y-%m-%d').date()
                    # Reformat to DD-MM-YYYY
                    field_value = date_object.strftime('%d-%m-%Y')
                except ValueError:
                    raise ValidationError(f"Invalid date format for field {field_name}. Date should be in DD-MM-YYYY format.")

            # Replace the original row value with the correctly formatted date
            row['Field Value'] = field_value  
    
class LanguageSkillResource(resources.ModelResource):  
    class Meta:
        model = LanguageSkill
        fields = ('language',)
        import_id_fields = ()
        

class MarketingSkillResource(resources.ModelResource):
    class Meta:
        model = MarketingSkill
        fields = ('marketing')
        import_id_fields = ()

class ProLangSkillResource(resources.ModelResource):
    class Meta:
        model = ProgrammingLanguageSkill
        fields = ('programming_language ')
        import_id_fields = ()

class EmpBankDetailsResource(resources.ModelResource):
    employee           = fields.Field(attribute='employee',column_name='Employee Code',widget=ForeignKeyWidget(emp_master, 'emp_code'))
    bank_name          = fields.Field(attribute='bank_name', column_name='Bank Name')
    branch_name        = fields.Field(attribute='branch_name', column_name='Branch Name')
    account_number     = fields.Field(attribute='account_number', column_name='Account Number')
    route_code         = fields.Field(attribute='route_code', column_name='Route Code')
    iban_number        = fields.Field(attribute='iban_number', column_name='IBAN/Account')
    class Meta:
        model = EmployeeBankDetail
        fields = ('employee', 'bank_name', 'branch_name','account_number','route_code','iban_number')
        import_id_fields = ()

    def before_import_row(self, row, **kwargs):
        errors = []  
        emp_code = row.get('Employee Code')

        if not emp_master.objects.filter(emp_code=emp_code).exists():
            errors.append(f"emp_master matching query does not exist for ID: {emp_code}")
        if errors:
            raise ValidationError(errors)
