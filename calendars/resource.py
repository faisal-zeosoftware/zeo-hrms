# resources.py
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget
from .models import Attendance,EmployeeMachineMapping,EmployeeShiftSchedule,emp_leave_balance,leave_type
from EmpManagement.models import emp_master
from django.core.exceptions import ValidationError
from datetime import datetime, timedelta, time

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

class MonthlyAttendanceResource(resources.ModelResource):
    class Meta:
        model = Attendance
        skip_unchanged = True
        report_skipped = True
        # no import_id_fields because row contains multiple attendances

    def import_data(self, dataset, dry_run=False, **kwargs):
        errors = []
        for row_number, row in enumerate(dataset.dict, start=1):
            identifier_code = row.get('Identifier Code')
            if not identifier_code:
                errors.append(f"Row {row_number}: Missing Identifier Code")
                continue

            # Get employee
            try:
                employee = emp_master.objects.get(emp_code=identifier_code)
            except emp_master.DoesNotExist:
                try:
                    mapping = EmployeeMachineMapping.objects.get(machine_code=identifier_code)
                    employee = mapping.employee
                except EmployeeMachineMapping.DoesNotExist:
                    errors.append(f"Row {row_number}: Employee not found for Identifier Code '{identifier_code}'")
                    continue

            year = row.get('Year')
            month_raw = row.get('Month')
            if not year or not month_raw:
                errors.append(f"Row {row_number}: Year or Month missing")
                continue

            try:
                year = int(year)
            except Exception:
                errors.append(f"Row {row_number}: Invalid Year '{year}'")
                continue

            month_str = str(month_raw).strip()

            for day in range(1, 32):
                in_key = f'{day}_In'
                out_key = f'{day}_Out'

                check_in_time = row.get(in_key)
                check_out_time = row.get(out_key)

                if not check_in_time and not check_out_time:
                    continue

                try:
                    if "-" not in month_str:
                        month_number = int(month_str)
                        date_obj = datetime(year, month_number, day).date()
                    else:
                        date_obj = datetime.strptime(f"{month_str}-{day:02d}", "%Y-%m-%d").date()
                except Exception as e:
                    errors.append(f"Row {row_number}, Day {day}: Invalid date - {e}")
                    continue

                try:
                    check_in = None
                    check_out = None

                    if check_in_time:
                        if isinstance(check_in_time, str):
                            check_in = datetime.strptime(check_in_time.strip(), "%H:%M:%S").time()
                        elif isinstance(check_in_time, time):
                            check_in = check_in_time
                        else:
                            raise ValueError(f"Invalid check-in time format: {check_in_time}")

                    if check_out_time:
                        if isinstance(check_out_time, str):
                            check_out = datetime.strptime(check_out_time.strip(), "%H:%M:%S").time()
                        elif isinstance(check_out_time, time):
                            check_out = check_out_time
                        else:
                            raise ValueError(f"Invalid check-out time format: {check_out_time}")

                except ValueError as e:
                    errors.append(f"Row {row_number}, Day {day}: Invalid time - {e}")
                    continue

                attendance = Attendance.objects.filter(employee=employee, date=date_obj).first()
                if not attendance:
                    attendance = Attendance(employee=employee, date=date_obj)

                attendance.check_in_time = check_in
                attendance.check_out_time = check_out

                # Assign shift
                schedule = EmployeeShiftSchedule.objects.filter(employee=employee).first()
                if schedule and hasattr(schedule, 'get_shift_for_date') and callable(schedule.get_shift_for_date):
                    try:
                        shift = schedule.get_shift_for_date(date_obj)
                        attendance.shift = shift
                    except Exception:
                        pass

                if check_in and check_out:
                    check_in_dt = datetime.combine(date_obj, check_in)
                    check_out_dt = datetime.combine(date_obj, check_out)
                    if check_out_dt < check_in_dt:
                        check_out_dt += timedelta(days=1)
                    attendance.total_hours = check_out_dt - check_in_dt

                attendance.save()

        if errors:
            raise ValidationError(errors)

        # Return super's import_data result for reporting
        return super().import_data(dataset, dry_run=dry_run, **kwargs)