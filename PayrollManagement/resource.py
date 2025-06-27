from .models import (EmployeeSalaryStructure,SalaryComponent)
from import_export import resources,fields
from django.core.exceptions import ValidationError
from import_export.widgets import ForeignKeyWidget
from EmpManagement .models import emp_master


class EmployeeSalaryStructureResource(resources.ModelResource):
    employee           = fields.Field(attribute='employee',column_name='Employee Code',widget=ForeignKeyWidget(emp_master, 'emp_code'))
    component          = fields.Field(attribute='component', column_name='Component',widget=ForeignKeyWidget(SalaryComponent, 'name'))
    amount             = fields.Field(attribute='amount', column_name='Amount')
    is_active          = fields.Field(attribute='is_active', column_name='Active')
    class Meta:
        model = EmployeeSalaryStructure
        fields = ('employee', 'component', 'amount','is_active')
        import_id_fields = ()

    def before_import_row(self, row, **kwargs):
        errors = []  
        emp_code = row.get('Employee Code')
        component = row.get('Component')
        if not emp_master.objects.filter(emp_code=emp_code).exists():
            errors.append(f"emp_master matching query does not exist for ID: {emp_code}")
        if not SalaryComponent.objects.filter(name=component).exists():
            errors.append(f"Salary Component matching query does not exist for ID: {emp_code}")
        if errors:
            raise ValidationError(errors)