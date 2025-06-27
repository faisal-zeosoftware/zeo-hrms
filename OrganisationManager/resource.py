from import_export import resources,fields
from import_export.admin import ImportMixin
from import_export.signals import post_import
from import_export.fields import Field
from .models import brnch_mstr,dept_master,desgntn_master,ctgry_master
from import_export.widgets import ForeignKeyWidget

class BranchResource(resources.ModelResource):
    class Meta:
        model = brnch_mstr
       
        fields = ('id',
                  'branch_name',
                  'branch_code',
                  'br_notification_period_days',
                  'br_start_date',
                  'br_is_active',
                  'br_country',
                  'br_state_id',
                  'br_city',
                  'br_pincode',
                  'br_branch_nmbr_1',
                  'br_branch_nmbr_2',
                  'br_branch_mail',
                                                            
        )

class DepartmentResource(resources.ModelResource):
    dept_name = fields.Field(attribute='dept_name', column_name='Department Name')
    dept_code = fields.Field(attribute='dept_code', column_name='Department Code')
    dept_description = fields.Field(attribute='dept_description', column_name='Description')
    dept_is_active = fields.Field(attribute='dept_is_active', column_name='Active')
    branch_id = fields.Field(attribute='branch_id', column_name='Branch',widget=ForeignKeyWidget(brnch_mstr, 'branch_name'))
    class Meta:
        model = dept_master
       
        fields = (
                  'dept_name',
                  'dept_code',
                  'dept_description',
                  'dept_is_active',
                  'branch_id'
        ) 
        import_id_fields = ()

class DeptReportResource(resources.ModelResource):
    dept_name = fields.Field(attribute='dept_name', column_name='Department Name')
    dept_code = fields.Field(attribute='dept_code', column_name='Department Code')
    dept_description = fields.Field(attribute='dept_description', column_name='Description')
    dept_is_active = fields.Field(attribute='dept_is_active', column_name='Active')
    branch_id = fields.Field(attribute='branch_id', column_name='Branch Code',widget=ForeignKeyWidget(brnch_mstr, 'branch_name'))
    class Meta:
        model = dept_master
       
        fields = (
                  'dept_name',
                  'dept_code',
                  'dept_description',
                  'dept_is_active',
                  'branch_id'
        ) 
class DesignationResource(resources.ModelResource):
    desgntn_job_title = fields.Field(attribute='desgntn_job_title', column_name='Job Tittle')
    desgntn_code = fields.Field(attribute='desgntn_code', column_name='Designation Code')
    desgntn_description = fields.Field(attribute='desgntn_description', column_name='Description')
    desgntn_is_active = fields.Field(attribute='desgntn_is_active', column_name='Active')


    class Meta:
        model = desgntn_master
       
        fields = (
                  'desgntn_job_title',
                  'desgntn_code',
                  'desgntn_description',
                  'desgntn_is_active',
                  
        )  
        import_id_fields = () 
    def before_import_row(self, row, **kwargs):
        # Check for missing 'Designation' field
        errors = []
        if not row.get('Job Tittle'):
            errors.append(f"Row {kwargs.get('row_number')}: 'Job Tittle' field cannot be blank.")
        if not row.get('Designation Code'):
            errors.append(f"Row {kwargs.get('row_number')}: 'Designation Code' field cannot be blank.")
        if desgntn_master.objects.filter(desgntn_code=row['Designation Code']).exists():
                errors.append(f"Designation Code '{row['Designation Code']}' Found Duplicate Designation Code - it must be unique .")
        if errors:
            raise ValueError(errors)          
class DesgtnReportResource(resources.ModelResource):
    desgntn_job_title = fields.Field(attribute='desgntn_job_title', column_name='Job Tittle')
    desgntn_code = fields.Field(attribute='desgntn_code', column_name='Designation Code')
    desgntn_description = fields.Field(attribute='desgntn_description', column_name='Description')
    desgntn_is_active = fields.Field(attribute='desgntn_is_active', column_name='Active')

    class Meta:
        model = desgntn_master
       
        fields = (
                  'desgntn_job_title',
                  'desgntn_code',
                  'desgntn_description',
                  'desgntn_is_active',
                  
        )  


class CategoryResource(resources.ModelResource):
    ctgry_title = fields.Field(attribute='ctgry_title', column_name='Category')
    ctgry_code = fields.Field(attribute='ctgry_code', column_name='Category Code')
    ctgry_description = fields.Field(attribute='ctgry_description', column_name='Description')
    ctgry_is_active = fields.Field(attribute='ctgry_is_active', column_name='Active') 
    class Meta:
        model = ctgry_master
       
        fields = (
                  'ctgry_title',
                  'ctgry_code',
                  'ctgry_description',
                  'ctgry_is_active',
                  
        )       
        import_id_fields = ()      


        