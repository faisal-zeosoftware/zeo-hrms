# resources.py
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, DateWidget, TimeWidget
from .models import Attendance, Shift, EmployeeMachineMapping,EmployeeShiftSchedule,leave_type,emp_leave_balance
from EmpManagement.models import emp_master
from django.core.exceptions import ValidationError
from datetime import datetime,time 
import datetime


class CustomEmployeeWidget(ForeignKeyWidget):
    def clean(self, value, row=None, *args, **kwargs):
        if not value:
            return None       
        try:
            return emp_master.objects.get(emp_code=value)
        except emp_master.DoesNotExist:
            try:
                mapping = EmployeeMachineMapping.objects.get(machine_code=value)
                return mapping.employee
            except EmployeeMachineMapping.DoesNotExist:
                error_msg = f"Identifier Code '{value}' does not exist in employee or machine code mappings."
                raise ValueError(error_msg)  # Raise ValueError with informative message
            except Exception as e:
                raise

class AttendanceResource(resources.ModelResource):
    employee = fields.Field(column_name='Identifier Code',attribute='employee',widget=CustomEmployeeWidget(emp_master, 'emp_code'))
    check_in_time = fields.Field(attribute='check_in_time', column_name='Check In Time')
    check_out_time = fields.Field(attribute='check_out_time', column_name='Check Out Time')
    date = fields.Field(attribute='date', column_name='Date')

    class Meta:
        model = Attendance
        fields = ('employee', 'check_in_time', 'check_out_time', 'date')
        import_id_fields = ('employee', 'date')  # Must be actual model fields

    def before_import_row(self, row, row_number=None, **kwargs):
        errors = []
        
        identifier_code = row.get('Identifier Code')
        date = row.get('Date')
        check_in_time = row.get('Check In Time')
        check_out_time = row.get('Check Out Time')
        employee = None
        if identifier_code:
            try:
                employee = emp_master.objects.get(emp_code=identifier_code)
            except emp_master.DoesNotExist:
                try:
                    mapping = EmployeeMachineMapping.objects.get(machine_code=identifier_code)
                    employee = mapping.employee
                except EmployeeMachineMapping.DoesNotExist:
                    pass  # We will handle below if employee is still None

        # STEP 2: Handle if employee not found
        if not employee:
            errors.append(f"Row {row_number}: Employee not found for Identifier Code '{identifier_code}'.")
        # # Validate employee code
        # employee = None
        # if isinstance(identifier_code, str):
        #     try:
        #         employee = emp_master.objects.get(emp_code=identifier_code)
        #         print("f",employee)
        #     except emp_master.DoesNotExist:
        #         try:
        #             mapping = EmployeeMachineMapping.objects.get(machine_code=identifier_code)
        #             employee = mapping.employee
        #         except EmployeeMachineMapping.DoesNotExist:
        #             errors.append(f"Identifier Code '{identifier_code}' does not exist.")
        
        # if not employee:
        #     errors.append(f"Employee not found for Identifier Code '{identifier_code}'.")

        if date and employee:
            if Attendance.objects.filter(employee=employee, date=date).exists():
                errors.append(f"Duplicate attendance record for Identifier '{identifier_code}' on {date}.")
        
        # Validate time fields
        for field, time_value in zip(['Check In Time', 'Check Out Time'], [check_in_time, check_out_time]):
            if isinstance(time_value, str):
                try:
                    row[field] = datetime.strptime(time_value, '%H:%M:%S').time()
                except ValueError:
                    errors.append(f"Invalid time format for {field}: {time_value}")

        # If employee is found, store it in the row dictionary
        if employee:
            row['employee'] = employee
        
        # Raise errors if any exist
        if errors:
            raise ValidationError(errors)
    def after_import_row(self, row, row_result, **kwargs):
        print("After import row called for:", row)
        employee = row.get('employee')
        date = row.get('Date')  # Ensure this is passed as a datetime object

        if employee and date:
            try:
                attendance = Attendance.objects.get(employee=employee, date=date)

                # Assign shift
                schedule = EmployeeShiftSchedule.objects.filter(employee=employee).first()
                if schedule:
                    shift = schedule.get_shift_for_date(date)  # Pass both employee and date
                    attendance.shift = shift

                # Calculate total hours
                attendance.calculate_total_hours()

                attendance.save()
            except Attendance.DoesNotExist:
                print(f"Attendance record not found for {employee} on {date}")

class EmployeeOpenBalanceResource(resources.ModelResource):
    employee            = fields.Field(attribute='employee',column_name='Employee Code',widget=ForeignKeyWidget(emp_master, 'emp_code'))
    leave_type          = fields.Field(attribute='leave_type', column_name='Leave Type',widget=ForeignKeyWidget(leave_type, 'name'))
    openings            = fields.Field(attribute='openings', column_name='Openings')
    is_active           = fields.Field(attribute='is_active', column_name='Active')
    class Meta:
        model = emp_leave_balance
        fields = ('employee', 'leave_type', 'openings','is_active')
        import_id_fields = ('employee', 'leave_type')
    
    def before_import_row(self, row, **kwargs):
        errors = []
        emp_code = row.get('Employee Code')
        leave_type_name = row.get('Leave Type')

        # Check employee exists
        if not emp_master.objects.filter(emp_code=emp_code).exists():
            errors.append(f"Employee with code '{emp_code}' does not exist.")

        # Check leave type exists
        if not leave_type.objects.filter(name=leave_type_name).exists():
            errors.append(f"Leave Type '{leave_type_name}' does not exist.")

        if errors:
            raise ValidationError(errors)

    def import_row(self, row, instance_loader, **kwargs):
        """
        Custom import logic: update openings and balance only if record exists
        """
        emp_code = row.get('Employee Code')
        leave_type_name = row.get('Leave Type')
        openings = row.get('Openings')

        try:
            employee = emp_master.objects.get(emp_code=emp_code)
            leave_type_obj = leave_type.objects.get(name=leave_type_name)

            # Try to find existing leave balance record
            try:
                leave_balance = emp_leave_balance.objects.get(employee=employee, leave_type=leave_type_obj)
                # Update openings and apply to balance
                leave_balance.openings = float(openings) if openings else 0
                leave_balance.apply_openings()
                leave_balance.is_active = row.get('Active', True)
                leave_balance.save()
            except emp_leave_balance.DoesNotExist:
                # If record does not exist, SKIP (Don't create new)
                pass

        except Exception as e:
            raise ValidationError(f"Error processing row for Employee {emp_code}: {str(e)}")

        # Return None because we handled saving manually
        return None