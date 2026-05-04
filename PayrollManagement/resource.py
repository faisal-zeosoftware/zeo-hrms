from .models import (EmployeeSalaryStructure,SalaryComponent)
from import_export import resources, fields, widgets
from django.core.exceptions import ValidationError
from import_export.widgets import ForeignKeyWidget
from EmpManagement .models import emp_master


class CleanDecimalWidget(widgets.DecimalWidget):
    def clean(self, value, row=None, *args, **kwargs):
        if value is not None:
            value = str(value).strip()
        if not value:
            return None
        return super().clean(value, row, *args, **kwargs)

class CleanBooleanWidget(widgets.BooleanWidget):
    def clean(self, value, row=None, *args, **kwargs):
        if value is not None:
            value = str(value).strip()
        if not value:
            return None
        return super().clean(value, row, *args, **kwargs)

class EmployeeSalaryStructureResource(resources.ModelResource):
    employee           = fields.Field(attribute='employee',column_name='Employee Code',widget=ForeignKeyWidget(emp_master, 'emp_code'))
    component          = fields.Field(attribute='component', column_name='Component',widget=ForeignKeyWidget(SalaryComponent, 'name'))
    amount             = fields.Field(attribute='amount', column_name='Amount', widget=CleanDecimalWidget())
    is_active          = fields.Field(attribute='is_active', column_name='Active', widget=CleanBooleanWidget())
    class Meta:
        model = EmployeeSalaryStructure
        fields = ('employee', 'component', 'amount','is_active')
        import_id_fields = ('employee','component')


    def before_import_row(self, row, **kwargs):
        # Clean whitespace and handle empty strings for numeric/boolean fields
        for key in ['Amount', 'Active']:
            if key in row and row[key] is not None:
                val = str(row[key]).strip()
                row[key] = None if val == "" else val

        errors = []  
        row_number = kwargs.get('row_idx', 'Unknown')

        # Validation: Amount cannot be empty and must be at least 0.00
        amount = row.get('Amount')
        if amount is None:
            errors.append(f"Row {row_number}: Amount cannot be empty")
        else:
            try:
                if float(amount) < 0:
                    errors.append(f"Row {row_number}: Amount must be at least 0.00")
            except (ValueError, TypeError):
                errors.append(f"Row {row_number}: Amount must be a valid number")

        emp_code = row.get('Employee Code')
        component = row.get('Component')
        if not emp_master.objects.filter(emp_code=emp_code).exists():
            errors.append(f"Row {row_number}: emp_master matching query does not exist for ID: {emp_code}")
        if not SalaryComponent.objects.filter(name=component).exists():
            errors.append(f"Row {row_number}: Salary Component matching query does not exist for ID: {component}")
        
        if errors:
            raise ValidationError(errors)


