import logging
from django.db import models
from django.db import models,transaction
from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from datetime import datetime, timedelta,timezone, time,date
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from EmpManagement.models import EmailConfiguration
from django.core.mail import EmailMultiAlternatives,get_connection, send_mail
from django.template import Context, Template
from django.utils.html import strip_tags
from django.utils import timezone
# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
from OrganisationManager.models import brnch_mstr,ctgry_master,dept_master
from EmpManagement.models import emp_master
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.db.models.signals import post_save
import calendar
from datetime import datetime, timedelta
from django.db.models import Q
from decimal import Decimal
from PayrollManagement .models import PayrollRun
from django.db.models import JSONField
from EmpManagement.utils import send_notification_email, get_employee_context


# Create your models here.
class weekend_calendar(models.Model):
    DAY_TYPE_CHOICES = [
        ('leave', 'Leave'),
        ('fullday', 'fullday'),
        ('halfday', 'Halfday'),
    ]
    description       = models.TextField()
    calendar_code     = models.CharField(max_length=100)
    year              = models.PositiveIntegerField()
    monday            = models.CharField(choices=DAY_TYPE_CHOICES,default='fullday')
    tuesday           = models.CharField(choices=DAY_TYPE_CHOICES,default='fullday')
    wednesday         = models.CharField(choices=DAY_TYPE_CHOICES,default='fullday')
    thursday          = models.CharField(choices=DAY_TYPE_CHOICES,default='fullday')
    friday            = models.CharField(choices=DAY_TYPE_CHOICES,default='fullday')
    saturday          = models.CharField(choices=DAY_TYPE_CHOICES,default='fullday')
    sunday            = models.CharField(choices=DAY_TYPE_CHOICES,default='fullday')
    is_alternate     = models.BooleanField(default=False)
    alternate_weekends = JSONField(
        default=dict, 
        blank=True,
        help_text="Example: { 'Friday': '1,3', 'Sunday': '2,4' }",null=True,
    )
    created_at        = models.DateTimeField(auto_now_add=True)
    created_by        = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    def __str__(self):
        return f"{self.calendar_code} - {self.year}"
    def is_weekend(self, date):
        """Check if the given date is a weekend based on the calendar configuration."""
        day_name = date.strftime('%A').lower()
        print("dayyy",day_name)
        day_type = getattr(self, day_name, 'fullday')
        return day_type == 'leave'
    def get_weekend_days(self):
        """Return list of day names that are marked as 'leave'."""
        days = {
            'Monday': self.monday,
            'Tuesday': self.tuesday,
            'Wednesday': self.wednesday,
            'Thursday': self.thursday,
            'Friday': self.friday,
            'Saturday': self.saturday,
            'Sunday': self.sunday,
        }
        return [day for day, value in days.items() if value == 'leave']
    def __str__(self):
        return f"{self.calendar_code} - {self.year}"
class WeekendDetail(models.Model):
    WEEKDAY_CHOICES = [
        ('Monday', 'Monday'),
        ('Tuesday', 'Tuesday'),
        ('Wednesday', 'Wednesday'),
        ('Thursday', 'Thursday'),
        ('Friday', 'Friday'),
        ('Saturday', 'Saturday'),
        ('Sunday', 'Sunday'),
    ]
    DAY_TYPE_CHOICES = [
        ('leave', 'Leave'),
        ('fullday', 'Full Day'),
        ('halfday', 'Half Day'),
    ]
    weekend_calendar = models.ForeignKey(weekend_calendar, related_name='details', on_delete=models.CASCADE)
    weekday          = models.CharField(max_length=9, choices=WEEKDAY_CHOICES)
    day_type         = models.CharField(max_length=7, choices=DAY_TYPE_CHOICES)
    week_of_month    = models.PositiveIntegerField(null=True, blank=True)  # 1 to 5 for specifying specific weeks
    month_of_year    = models.PositiveIntegerField(null=True, blank=True)
    date             = models.DateField(null=True, blank=True)  # Specific date for the day
    is_alternate     = models.BooleanField(default=False)
    alternate_pattern = models.CharField(max_length=50, null=True, blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    created_by       = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    class Meta:
        ordering = [ 'pk']
@receiver(post_save, sender=weekend_calendar)
def create_weekend_details(sender, instance, created, **kwargs):
    if not created:
        return

    day_types = {
        'monday': instance.monday,
        'tuesday': instance.tuesday,
        'wednesday': instance.wednesday,
        'thursday': instance.thursday,
        'friday': instance.friday,
        'saturday': instance.saturday,
        'sunday': instance.sunday,
    }

    year = instance.year
    start_date = datetime(year, 1, 1)
    end_date = datetime(year, 12, 31)

    # Convert alternates to dict {"saturday": [2,4]}
    alternate_patterns = {}
    for day, pattern in (instance.alternate_weekends or {}).items():
        alternate_patterns[day.lower()] = [
            int(x) for x in pattern.split(",") if x.strip().isdigit()
        ]

    current_date = start_date

    while current_date <= end_date:

        weekday_name = calendar.day_name[current_date.weekday()].lower()
        week_of_month = ((current_date.day - 1) // 7) + 1

        is_alternate = False
        alt_pattern = None

        # DEFAULT from main calendar
        final_day_type = day_types[weekday_name]

        # If the weekday has alternate weekend pattern
        if weekday_name in alternate_patterns:

            alt_weeks = alternate_patterns[weekday_name]

            # If this week is alternate → leave
            if week_of_month in alt_weeks:
                is_alternate = False
                alt_pattern = instance.alternate_weekends.get(weekday_name.capitalize())
                final_day_type = "leave"
            else:
                # Non alternate weeks must be fullday
                final_day_type = "fullday"

        # Create WeekendDetail
        WeekendDetail.objects.create(
            weekend_calendar=instance,
            weekday=weekday_name.capitalize(),
            day_type=final_day_type,
            week_of_month=week_of_month,
            month_of_year=current_date.month,
            date=current_date.date(),
            is_alternate=is_alternate,
            alternate_pattern=alt_pattern
        )

        current_date += timedelta(days=1)

    def __str__(self):
        return f"{self.weekday} - {self.day_type}"

class assign_weekend(models.Model):
    EMP_CHOICES = [
        ("branch", "Branch"),
        ("department", "Department"),
        ("category", "Category"),
        ("employee", "Employee"),
        ("designation", "Designation"),
    ]
    related_to    = models.CharField(max_length=20, choices=EMP_CHOICES,null=True)
    branch        = models.ManyToManyField('OrganisationManager.brnch_mstr',  null=True, blank=True)
    department    = models.ManyToManyField('OrganisationManager.dept_master',  null=True, blank=True)
    category      = models.ManyToManyField('OrganisationManager.ctgry_master', null=True, blank=True)
    designation      = models.ManyToManyField('OrganisationManager.desgntn_master',  null=True, blank=True)    
    employee      = models.ManyToManyField('EmpManagement.emp_master',  null=True, blank=True)
    weekend_model = models.ForeignKey(weekend_calendar,on_delete=models.CASCADE)
    created_at    = models.DateTimeField(auto_now_add=True)
    created_by    = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')


def assign_weekend_to_employees(employees, weekend_model):
    """
    Updates employee weekend calendar and yearly calendar.
    """
    # Update emp_weekend_calendar for all
    employees.update(emp_weekend_calendar=weekend_model)

    # Now update yearly calendar for each employee
    for emp in employees.iterator():
        update_employee_yearly_calendar(emp, weekend_model)

@receiver(m2m_changed, sender=assign_weekend.branch.through)
@receiver(m2m_changed, sender=assign_weekend.department.through)
@receiver(m2m_changed, sender=assign_weekend.category.through)
@receiver(m2m_changed, sender=assign_weekend.employee.through)
def update_weekend_assignment(sender, instance, action, **kwargs):

    if action not in ['post_add', 'post_remove', 'post_clear']:
        return
    
    weekend_model = instance.weekend_model

    # --- BRANCH BASED ---------------------------------------------------------
    if instance.related_to == "branch":
        branches = instance.branch.all()
        for branch in branches:
            employees = emp_master.objects.filter(emp_branch_id=branch.id)
            assign_weekend_to_employees(employees, weekend_model)

    # --- DEPARTMENT BASED -----------------------------------------------------
    elif instance.related_to == "department":
        departments = instance.department.all()
        for department in departments:
            employees = emp_master.objects.filter(emp_dept_id=department.id)
            assign_weekend_to_employees(employees, weekend_model)
    # --- Designation BASED -------------------------------------------------------
    elif instance.related_to == "designation":
        designations = instance.designation.all()
        for designation in designations:
            employees = emp_master.objects.filter(emp_desgntn_id=designation.id)
            assign_weekend_to_employees(employees, weekend_model)
    # --- CATEGORY BASED -------------------------------------------------------
    elif instance.related_to == "category":
        categories = instance.category.all()
        for category in categories:
            employees = emp_master.objects.filter(emp_ctgry_id=category.id)
            assign_weekend_to_employees(employees, weekend_model)

    # --- EMPLOYEE BASED -------------------------------------------------------
    elif instance.related_to == "employee":
        employees = instance.employee.all()
        assign_weekend_to_employees(employees, weekend_model)

def update_employee_yearly_calendar(employee, weekend_model):
    year = weekend_model.year
    # Check if EmployeeYearlyCalendar for this employee and year already exists
    try:
        yearly_calendar = EmployeeYearlyCalendar.objects.get(emp=employee, year=year)
        logger.debug(f"Found existing EmployeeYearlyCalendar for employee ID {employee.id} for year {year}")
    except EmployeeYearlyCalendar.DoesNotExist:
        yearly_calendar = EmployeeYearlyCalendar(emp=employee, year=year, daily_data={})
        logger.debug(f"Created new EmployeeYearlyCalendar for employee ID {employee.id} for year {year}")

    # Merge new weekend details into existing `daily_data` without overwriting existing data
    weekend_details = WeekendDetail.objects.filter(weekend_calendar=weekend_model)
    updated_data = yearly_calendar.daily_data  # Copy of existing data

    for detail in weekend_details:
        date_str = detail.date.strftime("%Y-%m-%d")
        # Only add or update if the date is not already set or if you need to update existing data
        if date_str not in updated_data or updated_data[date_str].get("status") != "Leave":
            updated_data[date_str] = {
                "status": "Leave" if detail.day_type == 'leave' else detail.day_type,
                "remarks": "Weekend assigned"
            }

    # Save the updated or newly created yearly calendar with merged data
    yearly_calendar.daily_data = updated_data
    yearly_calendar.save()
    logger.debug(f"Updated EmployeeYearlyCalendar for employee ID {employee.id} with new weekend data")

class holiday_calendar(models.Model):
    calendar_title  = models.CharField(max_length=50)
    year            = models.IntegerField()
    created_at      = models.DateTimeField(auto_now_add=True)
    created_by      = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    
    def __str__(self):
        return f"{self.calendar_title} - {self.year}"
    def is_holiday(self, date):
        # Logic to determine if 'date' is a holiday
        return self.holidays.filter(holiday_date=date).exists()
    # holiday         = models.ManyToManyField(holiday)

class holiday(models.Model):
    description = models.CharField(max_length=50,unique=True)
    start_date  = models.DateField()
    end_date    = models.DateField()
    calendar    = models.ForeignKey(holiday_calendar,on_delete=models.CASCADE,null=True,related_name='holiday_list')
    restricted  = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True)
    created_by  = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')



class assign_holiday(models.Model):
    EMP_CHOICES = [
        ("branch", "Branch"),
        ("department", "Department"),
        ("category", "Category"),
        ("employee", "Employee"),
    ]
    related_to     = models.CharField(max_length=20, choices=EMP_CHOICES,null=True)
    branch         = models.ManyToManyField('OrganisationManager.brnch_mstr',  null=True, blank=True)
    department    = models.ManyToManyField('OrganisationManager.dept_master',  null=True, blank=True)
    category      = models.ManyToManyField('OrganisationManager.ctgry_master', null=True, blank=True)
    employee       = models.ManyToManyField('EmpManagement.emp_master',  null=True, blank=True)
    holiday_model  = models.ForeignKey(holiday_calendar,on_delete=models.CASCADE)
    created_at     = models.DateTimeField(auto_now_add=True)
    created_by     = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')


@receiver(m2m_changed, sender=assign_holiday.branch.through)
def update_branch_holiday_calendar(sender, instance, action, **kwargs):
    if action in ['post_add', 'post_remove', 'post_clear'] and instance.related_to == "branch":
        branches = instance.branch.all()
        # logger.debug(f"Updating employees for branches: {[branch.id for branch in branches]}")
        for branch in branches:
            updated_count = emp_master.objects.filter(emp_branch_id=branch.id).update(holiday_calendar=instance.holiday_model)
            # logger.debug(f"Updated {updated_count} employees for branch ID {branch.id}")
@receiver(m2m_changed, sender=assign_weekend.department.through)
def update_department_weekend_calendar(sender, instance, action, **kwargs):
    if action in ['post_add', 'post_remove', 'post_clear'] and instance.related_to == "department":
        departments = instance.department.all()
        # logger.debug(f"Updating employees for departments: {[department.id for department in departments]}")
        for department in departments:
            updated_count = emp_master.objects.filter(emp_dept_id=department.id).update(holiday_calendar=instance.weekend_model)
            # logger.debug(f"Updated {updated_count} employees for department ID {department.id}")
@receiver(m2m_changed, sender=assign_weekend.category.through)
def update_category_weekend_calendar(sender, instance, action, **kwargs):
    if action in ['post_add', 'post_remove', 'post_clear'] and instance.related_to == "category":
        categories = instance.category.all()
        # logger.debug(f"Updating employees for categories: {[category.id for category in categories]}")
        for category in categories:
            updated_count = emp_master.objects.filter(emp_ctgry_id=category.id).update(holiday_calendar=instance.weekend_model)
            # logger.debug(f"Updated {updated_count} employees for category ID {category.id}")
@receiver(m2m_changed, sender=assign_holiday.employee.through)
def update_employee_weekend_calendar(sender, instance, action, **kwargs):
    if action in ['post_add', 'post_remove', 'post_clear'] and instance.related_to == "employee":
        employees = instance.employee.all()
        # logger.debug(f"Updating employees: {[employee.id for employee in employees]}")
        for employee in employees:
            employee.holiday_calendar = instance.holiday_model
            employee.save()
            # logger.debug(f"Updated employee ID {employee.id}")
def update_employee_yearly_calendar_with_holidays(employee, holiday_calendar):
    year = holiday_calendar.year
    print("holiday")
    try:
        yearly_calendar, created = EmployeeYearlyCalendar.objects.get_or_create(emp=employee, year=year)
        updated_data = yearly_calendar.daily_data
        for holiday in holiday_calendar.holiday.all():
            current_date = holiday.start_date
            while current_date <= holiday.end_date:
                date_str = current_date.strftime("%Y-%m-%d")
                # Only add holiday data if not already set or if replacing certain data is allowed
                if date_str not in updated_data or updated_data[date_str].get("status") != "Leave":
                    updated_data[date_str] = {
                        "status": "Leave" if holiday.restricted else "Holiday",
                        "remarks": holiday.description
                    }
                current_date += timedelta(days=1)
        print("holiday1")
        yearly_calendar.daily_data = updated_data
        yearly_calendar.save()

    except Exception as e:
        logger.error(f"Failed to update EmployeeYearlyCalendar for employee ID {employee.id}: {e}")

#leavemangement            
class leave_type(models.Model):
    type_choice =   [
        ('paid','paid'),
        ('unpaid','unpaid'),
    ]
    unit_choice = [
        ('days','days'),
        ('hours','hours'),
    ]

    balance_choice = [
        ('fixed','fixed'),
        ('leave_grant','leave_grant')
    ]
    CATEGORY_CHOICES = [
        ('annual', 'Annual Leave'),
        ('sick', 'Sick Leave'),
        ('casual', 'Casual Leave'),
        ('maternity', 'Maternity Leave'),
        ('paternity', 'Paternity Leave'),
        ('compensatory', 'Compensatory Leave'),
        ('bereavement', 'Bereavement Leave'),
        ('unpaid', 'Unpaid Leave'),
    ]
    name                          = models.CharField(max_length=50)
    # image                         = models.ImageField(upload_to='leave_images/')
    code                          = models.CharField(max_length=30)
    type                          = models.CharField(max_length=20,choices=type_choice)
    unit                          = models.CharField(max_length=10,choices=unit_choice)
    negative                      = models.BooleanField(default=False)
    description                   = models.CharField(max_length=200)  
    allow_half_day                = models.BooleanField(default=False)  # Allows half-day leave if set to True
    valid_from                    = models.DateField(null=True,blank=True)
    valid_to                      = models.DateField(null=True,blank=True)
    is_compensatory               = models.BooleanField(default=False)
    include_weekend               = models.BooleanField(default=False)
    include_holiday               = models.BooleanField(default=False)
    use_common_workflow           = models.BooleanField(default=False)
    include_dashboard             = models.BooleanField(default=False)
    enable_leave_pay_rule         = models.BooleanField(default=False, help_text="Enable pay rules based on leave duration")    
    is_entitlement                = models.BooleanField(default=False)
    branch                        = models.ForeignKey('OrganisationManager.brnch_mstr', on_delete=models.CASCADE,null=True,blank=True, related_name='leave_types')
    leave_category                = models.CharField(max_length=30,choices=CATEGORY_CHOICES,default='annual')
    created_at                    = models.DateTimeField(default=timezone.now)
    created_by                    = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['name', 'branch'], name='unique_leave_name_per_branch'),
            models.UniqueConstraint(fields=['code', 'branch'], name='unique_leave_code_per_branch'),
        ]
    def __str__(self):
        return f"{self.name}"
    def get_email_template(self, template_type):
        # Try fetching a specific template for the request type
        email_templates = self.email_templates.filter(template_type=template_type)

        # Check if there are multiple templates and handle appropriately
        if email_templates.count() > 1:
            raise ValueError(f"Multiple email templates found for template type '{template_type}' and request type '{self.name}'")
        elif email_templates.exists():
            return email_templates.first()


class leave_entitlement(models.Model):  
    EFFECTIVE_AFTER_CHOICES = [
        ('date_of_joining', 'Date of Joining'),
        ('date_of_confirmation', 'Date of Confirmation'),
    ]
    TIME_UNIT_CHOICES = [
        ('years', 'Years'),
        ('months', 'Months'),
        ('days','days')
    ]
    ROUND_OF_TYPE = [ 
        ('nearest_lowest','nearest_lowest'),
        ('nearest_highest','nearest_highest')
    ]
    DAY_CHOICES = [
        ('1st', '1st Day of the Month'),
        ('last', 'Last Day of the Month'),
        ('joining_day', 'Employee Joining Date')
    ]
    
    MONTH_CHOICES = [
        ('Jan', 'January'),
        ('Feb', 'February'),
        ('Mar', 'March'),
        ('Apr', 'April'),
        ('May', 'May'),
        ('Jun', 'June'),
        ('Jul', 'July'),
        ('Aug', 'August'),
        ('Sep', 'September'),
        ('Oct', 'October'),
        ('Nov', 'November'),
        ('Dec', 'December')
    ]
    Entitlement_TYPES = [
    ('fixed', 'Fixed'),
    ('variable', 'Variable'),]
    # PRORATE_CHOICES = [
    #     ('start_of_policy', 'Start of Policy'),
    #     ('start_and_end_of_policy', 'Start and End of Policy'),
    #     ('do_not_prorate', 'Do not Prorate')
    # ]
    leave_type                     = models.ForeignKey('leave_type', on_delete=models.CASCADE)
    min_experience                 = models.PositiveIntegerField(default=0,help_text="Minimum experience required.")
    effective_after_unit           = models.CharField(max_length=10, choices=TIME_UNIT_CHOICES, default='months')
    effective_after_from           = models.CharField(max_length=20, choices=EFFECTIVE_AFTER_CHOICES)
    accrual                        = models.BooleanField(default=False)
    accrual_rate                   = models.FloatField(default=0,null=True, blank=True, help_text="Accrual rate per period (e.g., days/months/yearly)")
    accrual_frequency              = models.CharField(max_length=20, choices=TIME_UNIT_CHOICES,null=True, blank=True)
    accrual_month                  = models.CharField(max_length=3, choices=MONTH_CHOICES, default='Jan',null=True,blank=True)
    accrual_day                    = models.CharField(max_length=20, choices=DAY_CHOICES, null=True, blank=True)
    # round_of                       = models.CharField(choices=ROUND_OF_TYPE,max_length=20)   
    prorate_accrual                = models.BooleanField(default=False, help_text="Enable prorate accrual for this leave type.")
    enable_leave_pay_rule          = models.BooleanField(default=False, help_text="Enable pay rules based on leave duration")       
    departments                    = models.ManyToManyField('OrganisationManager.dept_master', blank=True, related_name="lv_entitlement")
    branches                       = models.ManyToManyField('OrganisationManager.brnch_mstr',blank=True,related_name="lv_entitlement")
    designations                   = models.ManyToManyField('OrganisationManager.desgntn_master',blank=True,related_name="lv_entitlement")
    categories                     = models.ManyToManyField('OrganisationManager.ctgry_master',blank=True,related_name="lv_entitlement")
    entitlement_type = models.CharField(max_length=20,choices=Entitlement_TYPES,default='fixed')
    created_at                     = models.DateTimeField(auto_now_add=True)
    created_by                     = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    def __str__(self):
        return f"{self.leave_type.name} Entitlement"

    def experience_to_months(self, value, unit):
        """Convert experience value to months for uniform comparison."""
        if unit == "years":
            return value * 12
        elif unit == "months":
            return value
        elif unit == "days":
            return value / 30  # Approximate conversion of days to months
        return 0

    # def clean(self):
    #     """Prevent overlapping entitlements ONLY if they target the same employee group."""
        
    #     self_min_months = self.experience_to_months(
    #         self.min_experience,
    #         self.effective_after_unit
    #     )

    #     overlapping_entitlements = leave_entitlement.objects.filter(
    #         leave_type=self.leave_type,
    #         effective_after_from=self.effective_after_from
    #     ).exclude(id=self.id)

    #     for other in overlapping_entitlements:
    #         other_min_months = other.experience_to_months(
    #             other.min_experience,
    #             other.effective_after_unit
    #         )

    #         # Experience must match to even consider conflict
    #         if self_min_months != other_min_months:
    #             continue

    #         # ------ NEW ROLE/DEPT/CATEGORY/BRANCH CHECKS ------

    #         same_designation = (
    #             not self.designations.exists() and not other.designations.exists()
    #         ) or (
    #             self.designations.exists() and other.designations.exists() and 
    #             set(self.designations.all()) == set(other.designations.all())
    #         )

    #         same_department = (
    #             not self.departments.exists() and not other.departments.exists()
    #         ) or (
    #             self.departments.exists() and other.departments.exists() and 
    #             set(self.departments.all()) == set(other.departments.all())
    #         )

    #         same_category = (
    #             not self.categories.exists() and not other.categories.exists()
    #         ) or (
    #             self.categories.exists() and other.categories.exists() and 
    #             set(self.categories.all()) == set(other.categories.all())
    #         )

    #         same_branch = (
    #             not self.branches.exists() and not other.branches.exists()
    #         ) or (
    #             self.branches.exists() and other.branches.exists() and 
    #             set(self.branches.all()) == set(other.branches.all())
    #         )

    #         # If ALL FILTER GROUPS MATCH → conflict
    #         if same_designation and same_department and same_category and same_branch:
    #             raise ValidationError(
    #                 f"Entitlement conflict with ID {other.id}: "
    #                 f"same experience & same employee group."
    #             )

    def save(self, *args, **kwargs):
        # self.clean()  # Validate before saving
        super().save(*args, **kwargs)
@receiver(post_save, sender=leave_entitlement)
def update_leave_type_entitlement(sender, instance, created, **kwargs):
    if created:
        leave_type.objects.filter(
            id=instance.leave_type_id
        ).update(is_entitlement=True)
class LeavePayRule(models.Model):
    leave_type = models.ForeignKey(leave_type, on_delete=models.CASCADE, related_name="pay_rules", null=True)
    sequence = models.PositiveIntegerField(default=1, help_text="Order in which rule applies (e.g. 1 for First, 2 for Next)")
    days = models.PositiveIntegerField(help_text="Number of days for this slab")
    pay_percentage = models.PositiveIntegerField(help_text="Pay percentage (e.g., 100, 50, 0)")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    class Meta:
        ordering = ['sequence']

    def __str__(self):
        return f"{self.entitlement.leave_type.name} - Seq: {self.sequence} - {self.days} Days at {self.pay_percentage}%"

class LeaveResetPolicy(models.Model):
    TIME_UNIT_CHOICES = [
        ('years', 'Years'),
        ('months', 'Months'),
        ('days', 'Days')
    ]
    
    DAY_CHOICES = [
        ('1st', '1st Day of the Month'),
        ('last', 'Last Day of the Month'),
    ]

    MONTH_CHOICES = [
        ('Jan', 'January'), ('Feb', 'February'), ('Mar', 'March'), ('Apr', 'April'),
        ('May', 'May'), ('Jun', 'June'), ('Jul', 'July'), ('Aug', 'August'),
        ('Sep', 'September'), ('Oct', 'October'), ('Nov', 'November'), ('Dec', 'December')
    ]
    UNIT_CHOICES =[
        ('percentage','percentage'),
        ('unit','unit')
    ]
    CARRY_CHOICE = [
        ('carry_forward','carry forward'),
        ('carry_forward_with_expiry','carry forward with expiry')
    ]
    leave_type                     = models.ForeignKey('leave_type', on_delete=models.CASCADE,related_name='reset_policy',null=True,blank=True)
    leave_entitlement              = models.OneToOneField('leave_entitlement', on_delete=models.CASCADE, related_name='reset_policy', null=True, blank=True)
    reset                          = models.BooleanField(default=False)
    frequency                      = models.CharField(max_length=20, choices=TIME_UNIT_CHOICES,null=True, blank=True)
    month                          = models.CharField(max_length=30, choices=MONTH_CHOICES, null=True, blank=True)
    day                            = models.CharField(max_length=20, choices=DAY_CHOICES,null=True, blank=True)
    allow_cf                       = models.BooleanField(default=False)
    carry_forward_choice           = models.CharField(max_length=100,choices=CARRY_CHOICE,null=True, blank=True)
    cf_value                       = models.PositiveIntegerField(null=True, blank=True)
    cf_unit_or_percentage          = models.CharField(max_length=50,choices=UNIT_CHOICES,null=True, blank=True)
    cf_max_limit                   = models.PositiveIntegerField(null=True,blank=True)
    cf_expires_in_value            = models.PositiveIntegerField(null=True,blank=True)
    cf_time_choice                 = models.CharField(max_length=20,choices=TIME_UNIT_CHOICES,null=True,blank=True)
    allow_encashment               = models.BooleanField(default=False)
    encashment_value               = models.PositiveIntegerField(default=50,null=True, blank=True)
    encashment_unit_or_percentage  = models.CharField(max_length=50,choices=UNIT_CHOICES,null=True,blank=True)
    encashment_max_limit           = models.PositiveIntegerField(null=True,blank=True)
    opening_balance                = models.DecimalField(max_digits=10, decimal_places=2, default=0, null=True, blank=True)
    departments                    = models.ManyToManyField('OrganisationManager.dept_master', blank=True, related_name="lv_reset")
    branches                       = models.ManyToManyField('OrganisationManager.brnch_mstr',blank=True,related_name="lv_reset")
    designations                   = models.ManyToManyField('OrganisationManager.desgntn_master',blank=True,related_name="lv_reset")
    categories                     = models.ManyToManyField('OrganisationManager.ctgry_master',blank=True,related_name="lv_reset")

# from django.db.models import Q

class emp_leave_balance(models.Model):
    employee       = models.ForeignKey('EmpManagement.emp_master',on_delete=models.CASCADE)
    leave_type     = models.ForeignKey('leave_type',on_delete=models.CASCADE)
    balance        = models.FloatField(null=True,blank=True)
    openings       = models.IntegerField(null=True,blank=True)
    updated_at     = models.DateTimeField(auto_now=True)  # Track last update
    created_at     = models.DateTimeField(auto_now_add=True)
    created_by     = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    class Meta:
        unique_together = ('employee', 'leave_type')
        permissions = (
            ('import_emp_leave_balance', 'Can import lv balance '),
            # Add more custom permissions here
        )
    def is_weekend(self, date):
        """ Check if the given date is a weekend based on the employee's weekend calendar """
        if self.employee.emp_weekend_calendar:
            # Assuming emp_weekend_calendar has a method is_weekend
            return self.employee.emp_weekend_calendar.is_weekend(date)
        return False

    def is_holiday(self, date):
        """ Check if the given date is a holiday based on the employee's holiday calendar """
        if self.employee.holiday_calendar:
            # Assuming holiday_calendar has a method is_holiday
            return self.employee.holiday_calendar.is_holiday(date)
        return False

    def get_leave_days(self, start_date, end_date):
        """ Calculate total leave days between start and end date, excluding weekends and holidays if applicable """
        total_days = 0
        current_date = start_date
        while current_date <= end_date:
            is_weekend = self.is_weekend(current_date)
            is_holiday = self.is_holiday(current_date)

            if self.leave_type.include_weekend_and_holiday:
                # Include both weekends and holidays
                total_days += 1
            else:
                # Exclude weekends and holidays
                if not is_weekend and not is_holiday:
                    total_days += 1

            current_date += timedelta(days=1)

        return total_days

    def deduct_leave(self, start_date, end_date, is_half_day=False):
        """ Deduct leave from balance, considering half-day and whether weekends/holidays are included """
        if is_half_day:
            leave_days = 0.5
        else:
            leave_days = self.get_leave_days(start_date, end_date)

        self.balance -= leave_days
        self.save()
    
    def save(self, *args, **kwargs):
        # Save normally without modifying balance
        super().save(*args, **kwargs)

    def apply_openings(self):
        """Use this method to apply openings to balance when needed."""
        if self.openings and self.openings > 0:
            self.balance = (self.balance or 0) + self.openings
            self.openings = 0
            self.save(update_fields=['balance', 'openings'])  # Save only these two fields

from django.db import models
from django.core.validators import MinValueValidator

class leave_accrual_transaction(models.Model):
    employee      = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE)
    leave_type    = models.ForeignKey(leave_type, on_delete=models.CASCADE)
    accrual_date  = models.DateField()
    amount        = models.DecimalField(max_digits=5, decimal_places=2)
    year          = models.PositiveIntegerField(default=datetime.now().year)
    created_at    = models.DateTimeField(auto_now_add=True)
    created_by    = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')


class leave_reset_transaction(models.Model):
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE)
    leave_type = models.ForeignKey('leave_type', on_delete=models.CASCADE)
    reset_date = models.DateField()
    initial_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # Balance before reset
    carry_forward_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    encashment_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    opening_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    final_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # Balance after reset
    year = models.PositiveIntegerField(default=datetime.now().year)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        'UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='reset_created_by'
    )
    def __str__(self):
        return f"{self.employee} - {self.leave_type} Reset on {self.reset_date}"
class LeaveCarryForwardTransaction(models.Model):
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE)
    leave_type = models.ForeignKey('leave_type', on_delete=models.CASCADE)
    reset_date = models.DateField()
    carried_forward_units = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    carried_forward_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    max_limit = models.DecimalField(max_digits=10, decimal_places=2, default=0,null=True,blank=True)  # Maximum allowed carry forward
    final_carry_forward = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # Final applied value
    year = models.PositiveIntegerField(default=datetime.now().year)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        'UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='carry_forward_created_by'
    )
class LeaveEncashmentTransaction(models.Model):
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE)
    leave_type = models.ForeignKey('leave_type', on_delete=models.CASCADE)
    reset_date = models.DateField()
    encashment_units = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    encashment_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    max_limit = models.DecimalField(max_digits=10, decimal_places=2, default=0,null=True,blank=True)  # Maximum allowed encashment
    encashment_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # Final applied encashment
    year = models.PositiveIntegerField(default=datetime.now().year)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        'UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='encashment_created_by'
    )    

class applicablity_critirea(models.Model):
    GENDER_CHOICES = [
        ("M", "Male"),
        ("F", "Female"),
        ("B", "Both"),
    ]
    
    leave_type   = models.ForeignKey(leave_type,on_delete=models.CASCADE)
    gender       = models.CharField(choices=GENDER_CHOICES,null=True,blank=True)
    branch       = models.ManyToManyField('OrganisationManager.brnch_mstr',blank=True)
    department   = models.ManyToManyField('OrganisationManager.dept_master',blank=True)
    designation  = models.ManyToManyField('OrganisationManager.desgntn_master',blank=True)
    role         = models.ManyToManyField('OrganisationManager.ctgry_master',blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    created_by   = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')


class LvEmailTemplate(models.Model):
    template_type = models.CharField(max_length=50, choices=[
        ('request_created', 'Request Created'),
        ('request_approved', 'Request Approved'),
        ('request_rejected', 'Request Rejected')
    ])
    subject     = models.CharField(max_length=255)
    body        = models.TextField()
    branch              = models.ManyToManyField('OrganisationManager.brnch_mstr',blank=True)
    Department          = models.ManyToManyField('OrganisationManager.dept_master',blank=True)
    Category            = models.ManyToManyField('OrganisationManager.ctgry_master',blank=True)
    Designation         = models.ManyToManyField('OrganisationManager.desgntn_master',blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    created_by  = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    
class LvApprovalNotify(models.Model):
    recipient_user     = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True,on_delete=models.CASCADE)
    recipient_employee = models.ForeignKey('EmpManagement.emp_master', null=True, blank=True, on_delete=models.CASCADE)
    message            = models.CharField(max_length=255)
    created_at         = models.DateTimeField(auto_now_add=True)
    is_read            = models.BooleanField(default=False)
    deligate_user = models.ForeignKey('UserManagement.CustomUser',null=True,blank=True,on_delete=models.CASCADE,related_name='leave_deligated_notifications')

    def __str__(self):
        if self.recipient_user:
            return f"Notification for {self.recipient_user.username}: {self.message}"
        else:
            return f"Notification for employee: {self.message}"    
    
    def send_email_notification(self, template_type, context):
        try:
            # Try to retrieve the active email configuration
            try:
                email_config = EmailConfiguration.objects.get(is_active=True)
                use_custom_config = True
            except EmailConfiguration.DoesNotExist:
                use_custom_config = False
                default_email = settings.EMAIL_HOST_USER

            # Use custom or default email configuration
            if use_custom_config:
                default_email = email_config.email_host_user
                connection = get_connection(
                    host=email_config.email_host,
                    port=email_config.email_port,
                    username=email_config.email_host_user,
                    password=email_config.email_host_password,
                    use_tls=email_config.email_use_tls,
                )
            else:
                connection = get_connection(
                    host=settings.EMAIL_HOST,
                    port=settings.EMAIL_PORT,
                    username=settings.EMAIL_HOST_USER,
                    password=settings.EMAIL_HOST_PASSWORD,
                    use_tls=settings.EMAIL_USE_TLS,
                )

            # Determine recipient email and name
            to_email = None
            recipient_name = None
            if self.recipient_user and self.recipient_user.email:
                to_email = self.recipient_user.email
                recipient_name = self.recipient_user.username
            elif self.recipient_employee and self.recipient_employee.emp_personal_email:
                to_email = self.recipient_employee.emp_personal_email
                recipient_name = self.recipient_employee.emp_first_name

            if to_email:
                context.update({'recipient_name': recipient_name})

                # Fetch the email template
                try:
                    email_template = LvEmailTemplate.objects.get(template_type=template_type)
                    subject = email_template.subject
                    template = Template(email_template.body)
                    html_message = template.render(Context(context))
                    plain_message = strip_tags(html_message)
                except LvEmailTemplate.DoesNotExist:
                    raise ValidationError("Email template not found. Please set an email template for this notification type.")

                # Send the email
                email = EmailMultiAlternatives(
                    subject=subject,
                    body=plain_message,
                    from_email=default_email,  # From email
                    to=[to_email],  # Recipient list
                    connection=connection,
                    headers={'From': 'zeosoftware@abc.com'}  # Custom header
                )
                email.attach_alternative(html_message, "text/html")
                email.send(fail_silently=False)

        except ValidationError as e:
            print(f"Validation Error: {e}")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
class LvCommonWorkflow(models.Model):
    level        = models.IntegerField()
    role         = models.CharField(max_length=50, null=True, blank=True)
    approver     = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True, on_delete=models.SET_NULL)
    created_at   = models.DateTimeField(auto_now_add=True)
    created_by   = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['level'], name='Lv_common_workflow_level')
        ]
    def __str__(self):
        return f"Level {self.level} - {self.role or self.approver}"

#compensatory leave

class CompensatoryLeaveAllocation(models.Model):
    employee        = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE)
    attendances      = models.ManyToManyField('Attendance', related_name='compensatory_allocations')
    reason          = models.TextField()
    credited_days   = models.FloatField(default=1.0)
    created_at      = models.DateTimeField(auto_now_add=True)
    is_allocated = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        'UserManagement.CustomUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    def __str__(self):
        return f"Allocation for {self.employee}"

class CompensatoryLeaveTransaction(models.Model):
    """Logs the addition and deduction of compensatory leave days."""
    TRANSACTION_TYPE_CHOICES = [
        ('addition', 'Addition'),
        ('deduction', 'Deduction'),
    ]
    
    employee         = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE)
    transaction_date = models.DateField(auto_now_add=True)
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPE_CHOICES)
    days             = models.FloatField()
    reason           = models.TextField()
    created_at       = models.DateTimeField(auto_now_add=True)
    created_by       = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')


    def __str__(self):
        return f"{self.employee} - {self.transaction_type} of {self.days} days on {self.transaction_date}"
     
class CompensatoryLeaveBalance(models.Model):
    """Tracks the total compensatory leave balance for each employee."""
    employee = models.OneToOneField('EmpManagement.emp_master', on_delete=models.CASCADE)
    balance  = models.FloatField(default=0)

    def __str__(self):
        return f"{self.employee} - Compensatory Balance: {self.balance} days"

class CompensatoryLeaveRequest(models.Model):
    LEAVE_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    REQUEST_TYPE_CHOICES = [
        ('work_request', 'Work Request'),
        ('leave_request', 'Compensatory Leave Request'),
    ]
    request_type    = models.CharField(max_length=15, choices=REQUEST_TYPE_CHOICES, default='work_request')
    employee        = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE)
    request_date    = models.DateField(auto_now_add=True)
    work_date       = models.DateField()  # Date employee worked on weekend/holiday
    reason          = models.TextField()
    status          = models.CharField(max_length=10, choices=LEAVE_STATUS_CHOICES, default='pending')
    created_at         = models.DateTimeField(auto_now_add=True)
    created_by         = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    def __str__(self):
        return f"Compensatory Request for {self.employee} on {self.work_date} - {self.status}"
    def save(self, *args, **kwargs):
        # Fetch the existing status before saving
        old_status = None
        if self.pk:
            old_status = CompensatoryLeaveRequest.objects.get(pk=self.pk).status

        # Call the original save method
        super().save(*args, **kwargs)

        # Proceed only if the request is approved and status has changed to approved
        if self.status == 'Approved' and old_status != 'Approved':
            # Wrap balance updates and transaction creation in an atomic transaction
            with transaction.atomic():
                # Fetch or create a compensatory leave balance record for the employee
                leave_balance, created = CompensatoryLeaveBalance.objects.get_or_create(employee=self.employee)

                if self.request_type == 'work_request':
                    # Add 1 day to balance for approved work requests
                    leave_balance.balance += 1
                    # Log the addition transaction
                    CompensatoryLeaveTransaction.objects.create(
                        employee=self.employee,
                        transaction_type='addition',
                        days=1,
                        reason=f"Approved work request on {self.work_date}"
                    )
                elif self.request_type == 'leave_request':
                    # Deduct 1 day from balance for approved leave requests
                    if leave_balance.balance >= 1:
                        leave_balance.balance -= 1
                        # Log the deduction transaction
                        CompensatoryLeaveTransaction.objects.create(
                            employee=self.employee,
                            transaction_type='deduction',
                            days=1,
                            reason=f"Approved compensatory leave on {self.work_date}"
                        )
                    else:
                        raise ValueError("Insufficient compensatory leave balance for this request.")

                # Save the updated leave balance
                leave_balance.save()


    def move_to_next_level(self):
        if self.approvals.filter(status=LeaveApproval.REJECTED).exists():
            self.status = 'Rejected'
            self.save()

            # Notify creator about rejection
            notification = LvApprovalNotify.objects.create(
                recipient_user=self.created_by,
                message=f"Your compensatory leave request for {self.work_date} has been rejected."
            )
            notification.send_email_notification('request_rejected', {
                'request_type': 'Compensatory Leave',
                'rejection_reason': 'Reason for rejection...',
                'work_date': self.work_date,
                'employee_name': self.employee.emp_first_name,
                'emp_gender': self.employee.emp_gender,
                'emp_date_of_birth': self.employee.emp_date_of_birth,
                'emp_personal_email': self.employee.emp_personal_email,
                'emp_company_email': self.employee.emp_company_email,
                'emp_branch_name': self.employee.emp_branch_id,
                'emp_department_name': self.employee.emp_dept_id,
                'emp_designation_name': self.employee.emp_desgntn_id,
            })
            return

        # Check current approval level and set up the next level
        current_approved_levels = self.approvals.filter(status=LeaveApproval.APPROVED).count()
        next_level = LeaveApprovalLevels.objects.filter(is_compensatory=True, level=current_approved_levels + 1).first()

        if next_level:
            last_approval = self.approvals.order_by('-level').first()
            LeaveApproval.objects.create(
                compensatory_request=self,
                approver=next_level.approver,
                role=next_level.role,
                level=next_level.level,
                status=LeaveApproval.PENDING,
                note=last_approval.note if last_approval else None
            )

            # Notify next approver
            notification = LvApprovalNotify.objects.create(
                recipient_user=next_level.approver,
                message=f"New compensatory leave request for approval: work date {self.work_date}, employee: {self.employee}"
            )
            notification.send_email_notification('request_created', {
                'request_type': 'Compensatory Leave',
                'employee_name': self.employee.emp_first_name,
                'reason': self.reason,
                'note': last_approval.note if last_approval else None,
                'emp_gender': self.employee.emp_gender,
                'emp_date_of_birth': self.employee.emp_date_of_birth,
                'emp_personal_email': self.employee.emp_personal_email,
                'emp_company_email': self.employee.emp_company_email,
                'emp_branch_name': self.employee.emp_branch_id,
                'emp_department_name': self.employee.emp_dept_id,
                'emp_designation_name': self.employee.emp_desgntn_id,
            })
        else:
            # Final approval reached, mark as approved and notify creator
            self.status = 'Approved'
            self.save()

            notification = LvApprovalNotify.objects.create(
                recipient_user=self.created_by,
                message=f"Your compensatory leave request for {self.work_date} has been approved."
            )
            notification.send_email_notification('request_approved', {
                'request_type': 'Compensatory Leave',
                'emp_gender': self.employee.emp_gender,
                'emp_date_of_birth': self.employee.emp_date_of_birth,
                'emp_personal_email': self.employee.emp_personal_email,
                'emp_company_email': self.employee.emp_company_email,
                'emp_branch_name': self.employee.emp_branch_id,
                'emp_department_name': self.employee.emp_dept_id,
                'emp_designation_name': self.employee.emp_desgntn_id,
            })
            if self.employee:
                notification = LvApprovalNotify.objects.create(
                    recipient_employee=self.employee,
                    message=f"Your compensatory leave request for {self.work_date} has been approved."
                )
                notification.send_email_notification('request_approved', {
                    'request_type': 'Compensatory Leave',
                    'emp_gender': self.employee.emp_gender,
                    'emp_date_of_birth': self.employee.emp_date_of_birth,
                    'emp_personal_email': self.employee.emp_personal_email,
                    'emp_company_email': self.employee.emp_company_email,
                    'emp_branch_name': self.employee.emp_branch_id,
                    'emp_department_name': self.employee.emp_dept_id,
                    'emp_designation_name': self.employee.emp_desgntn_id,
                })
@receiver(post_save, sender=CompensatoryLeaveRequest)
def create_initial_approval_for_compensatory_leave(sender, instance, created, **kwargs):
    if created:
        # Fetch the first level for compensatory leave
        first_level = LeaveApprovalLevels.objects.filter(is_compensatory=True).order_by('level').first()

        if first_level:
            LeaveApproval.objects.create(
                compensatory_request=instance,
                approver=first_level.approver,
                role=first_level.role,
                level=first_level.level,
                status=LeaveApproval.PENDING
            )
        # Notify first approver
            notification = LvApprovalNotify.objects.create(
                recipient_user=first_level.approver,
                message=f"New request for approval: Compensatory Leave, employee: {instance.employee}"
            )
            notification.send_email_notification('request_created', {
                'request_type': 'Compensatory Leave',
                'employee_name': instance.employee.emp_first_name,
                'reason': instance.reason,
                'emp_gender':instance.employee.emp_gender,
                'emp_date_of_birth':instance.employee.emp_date_of_birth,
                'emp_personal_email':instance.employee.emp_personal_email,
                'emp_company_email':instance.employee.emp_company_email,
                'emp_branch_name':instance.employee.emp_branch_id,
                'emp_department_name':instance.employee.emp_dept_id,
                'emp_designation_name':instance.employee.emp_desgntn_id,
            }) 

class employee_leave_request(models.Model):
    LEAVE_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    HALF_DAY_CHOICES = [
        ('first_half', 'First Half'),
        ('second_half', 'Second Half'),
    ]
    
    employee          = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE)
    branch           =  models.ForeignKey('OrganisationManager.brnch_mstr',on_delete=models.SET_NULL, null=True)
    leave_type        = models.ForeignKey(leave_type, on_delete=models.CASCADE)    
    start_date        = models.DateField()
    end_date          = models.DateField()
    reason            = models.TextField()
    status            = models.CharField(max_length=10, choices=LEAVE_STATUS_CHOICES, default='pending')
    applied_on        = models.DateField(auto_now_add=True)
    document_number   = models.CharField(max_length=50, unique=True, blank=True)
    dis_half_day      = models.BooleanField(default=False)  # True if it's a half-day leave
    half_day_period   = models.CharField(max_length=20, choices=HALF_DAY_CHOICES, null=True, blank=True)  # First Half / Second Half
    created_by        = models.ForeignKey('UserManagement.CustomUser',on_delete=models.CASCADE,null=True,blank=True)
    number_of_days    = models.FloatField(default=1)
    applied_days      = models.FloatField(default=0)   # Total days employee requested
    approved_days     = models.FloatField(default=0)  # Days actually approved
    lv_document       = models.FileField(upload_to="leaverequest_documents/",null=True,blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)
    created_by        = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    class Meta:
        permissions = (
                ("add_lv_cancellation", "Can add leave cancellation"),
                ("view_lv_cancellation", "Can view leave cancellation"),
                # ("export_lv_cancellation", "Can export leave cancellation"),
                ("delete_lv_cancellation", "Can delete leave cancellation"),
        )
    def clean(self):
        super().clean()
        # --- PAYROLL LOCK VALIDATION ---
        leave_month = self.start_date.month
        leave_year = self.start_date.year
        emp = self.employee

        # 🔹 Case 1: Employee included directly in a payroll run
        payroll_exists = PayrollRun.objects.filter(
            month=leave_month,
            year=leave_year,
            employees=emp,
            status__in=['processed', 'approved', 'paid']
        ).exists()

        # 🔹 Case 2: Payroll run is for branch/department/category where employee belongs
        if not payroll_exists:
            payroll_exists = PayrollRun.objects.filter(
                month=leave_month,
                year=leave_year,
                branch=emp.emp_branch_id,
                department=emp.emp_dept_id,
                category=emp.emp_ctgry_id,
                status__in=['processed', 'approved', 'paid']
            ).exists()

        if payroll_exists:
            raise ValidationError(
                f"Leave cannot be created/edited because payroll for "
                f"{leave_month}/{leave_year} is already processed."
            )
        # Validate if half-day leave is allowed for this leave type
        if self.dis_half_day and not self.leave_type.allow_half_day:
            raise ValidationError(f"{self.leave_type} does not allow half-day leaves.")

        # If half-day leave is chosen, ensure the date range is correct
        if self.dis_half_day and self.start_date != self.end_date:
            raise ValidationError("Half-day leave should be on the same day.")
        # Calculate number of leave days
        leave_days_requested = self.calculate_leave_days()
        #
        if self.leave_type.type == 'unpaid':
            return
        # Fetch or create leave balance for the employee
        leave_balance, created = emp_leave_balance.objects.get_or_create(
            employee=self.employee,
            leave_type=self.leave_type
        )

        # Check if leave type does not allow negative balance and employee has insufficient balance
        if not self.leave_type.negative and leave_balance.balance < leave_days_requested:
            raise ValidationError("Insufficient leave balance for this leave type.")
        

    def save(self, *args, **kwargs):
        # Calculate leave days based on start and end date
        self.number_of_days = self.calculate_leave_days()
        # If applied_days not set, initialize with requested number_of_days
        if not self.applied_days or self.applied_days == 0:
            self.applied_days = self.number_of_days
        self.clean()
        # Check if the status changed to "approved"
        previous_instance = type(self).objects.filter(pk=self.pk).first()
        status_changed_to_approved = (
            previous_instance is None or previous_instance.status != 'approved'
        ) and self.status == 'approved'
        status_changed_to_rejected = (
        previous_instance and previous_instance.status == 'approved'
        ) and self.status == 'rejected'
        print("s",status_changed_to_approved)
        print("sr",status_changed_to_rejected)
        with transaction.atomic():
            super().save(*args, **kwargs)
            if status_changed_to_approved:
                self.deduct_leave_balance()
            # elif status_changed_to_rejected:
            #     self.restore_leave_balance()
            
    def calculate_leave_days(self):
        from .utils import get_employee_holiday_calendar,get_employee_weekend_calendar
        leave_days = 0
        current_date = self.start_date

        # NEW: read the separated fields
        include_weekend = self.leave_type.include_weekend
        include_holiday = self.leave_type.include_holiday

        # Weekend Calendar
        assigned_weekend = get_employee_weekend_calendar(self.employee)
        weekend_days = []

        if assigned_weekend:
            # map weekend model boolean fields to weekday names
            weekend_days = [
                day for day, value in {
                    'monday': assigned_weekend.monday,
                    'tuesday': assigned_weekend.tuesday,
                    'wednesday': assigned_weekend.wednesday,
                    'thursday': assigned_weekend.thursday,
                    'friday': assigned_weekend.friday,
                    'saturday': assigned_weekend.saturday,
                    'sunday': assigned_weekend.sunday,
                }.items() if value == 'leave'
            ]

        # Holiday Calendar
        assigned_holiday = get_employee_holiday_calendar(self.employee)
        holiday_dates = set()

        if assigned_holiday:
            holiday_dates = set(
                assigned_holiday.holiday_list.all().values_list('start_date', flat=True)
            )

        # -------------------- DATE LOOP --------------------
        while current_date <= self.end_date:

            weekday_name = current_date.strftime('%A').lower()

            is_weekend_day = weekday_name in weekend_days
            is_holiday_day = current_date in holiday_dates

            # ---------- Weekend Filter ----------
            if is_weekend_day and not include_weekend:
                current_date += timedelta(days=1)
                continue

            # ---------- Holiday Filter ----------
            if is_holiday_day and not include_holiday:
                current_date += timedelta(days=1)
                continue

            # ---------- Half-day Leave ----------
            if (
                self.dis_half_day
                and self.start_date == self.end_date == current_date
            ):
                leave_days += 0.5
            else:
                leave_days += 1

            current_date += timedelta(days=1)

        return leave_days
    def deduct_leave_balance(self):
        from decimal import Decimal
        leave_balance, created = emp_leave_balance.objects.get_or_create(
            employee=self.employee,
            leave_type=self.leave_type
        )

        # Deduct based on approved_days, fallback to number_of_days
        days_to_deduct = self.approved_days or self.number_of_days
        leave_balance.balance -= days_to_deduct
        leave_balance.save()

        # Deduct from carry forward if exists
        leave_days_to_deduct = Decimal(str(days_to_deduct))
        carry_forward_entry = LeaveCarryForwardTransaction.objects.filter(
            employee=self.employee,
            leave_type=self.leave_type,
            final_carry_forward__gt=0
        ).order_by('-reset_date').first()

        if carry_forward_entry:
            carry_forward_entry.final_carry_forward -= leave_days_to_deduct
            carry_forward_entry.save()

    def restore_leave_balance(self):
        from decimal import Decimal

        leave_balance, created = emp_leave_balance.objects.get_or_create(
            employee=self.employee,
            leave_type=self.leave_type
        )

        leave_balance.balance += self.number_of_days
        leave_balance.save()

        # Restore in carry forward if it was deducted
        leave_days_to_restore = Decimal(str(self.number_of_days))
        carry_forward_entry = LeaveCarryForwardTransaction.objects.filter(
            employee=self.employee,
            leave_type=self.leave_type
        ).order_by('-reset_date').first()

        if carry_forward_entry:
            carry_forward_entry.final_carry_forward += leave_days_to_restore
            carry_forward_entry.save()
    def __str__(self):
        # return f"{self.employee} {self.document_number} - {self.leave_type} from {self.start_date} to {self.end_date}"
        return f"{self.document_number} "
    
    
    # def get_employee_requests(employee_id):
    #     return employee_leave_request.objects.filter(employee_id=employee_id).order_by('-applied_on')
     
    def move_to_next_level(self):

        # ---------------- REJECT ---------------- #
        if self.approvals.filter(status=LeaveApproval.REJECTED).exists():
            self.status = 'rejected'
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Request Rejected",
                notification_type="leave_request",
                message=(f"Your LeaveRequest {self.leave_type}"
                        f"(Document No: {self.document_number}) has been Rejected."
                    ),
                template_type="request_rejected",
                context={
                    **get_employee_context(self.employee),
                    'request_type': self.leave_type.name,
                },
                email_template_model=LvEmailTemplate,
                notification_model= LvApprovalNotify,
            )

            return

        # ---------------- GET WORKFLOW ---------------- #
        workflow = LVApprovalWorkflow.objects.filter(
            request_type=self.leave_type,
            branch__in=[self.employee.emp_branch_id]
        ).first()

        # ---------------- NO WORKFLOW ---------------- #
        if not workflow:
            self.status = 'approved'
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Request Approved",
                notification_type="lv_request",
                message=(f"Your LeaveRequest {self.leave_type}"
                        f"(Document No: {self.document_number}) has been Approved."
                    ),
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    'request_type': self.leave_type.name,
                },
                email_template_model=LvEmailTemplate,
                notification_model= LvApprovalNotify,
            )

            return

        approval_type = workflow.approval_type


        # =========================================================
        # MINIMUM APPROVAL CHECK (GENERAL REQUEST STYLE)
        # =========================================================

        approved_count = self.approvals.filter(
            status=LeaveApproval.APPROVED
        ).count()

        min_required = getattr(workflow, 'min_approvals_required', None)

        if min_required and approved_count >= min_required:
            self.status = 'approved'
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Request Approved",
                notification_type="lv_request",
                message=(f"Your LeaveRequest {self.leave_type}"
                        f"(Document No: {self.document_number}) has been Approved."
                    ),
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    'request_type': self.leave_type.name,
                },
                email_template_model=LvEmailTemplate,
                notification_model= LvApprovalNotify,
            )

            return

        # ---------------- NO APPROVAL ---------------- #
        if approval_type == 'no_approval':
            self.status = 'approved'
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Request Approved",
                notification_type="lv_request",
                message=(f"Your LeaveRequest {self.leave_type}"
                        f"(Document No: {self.document_number}) has been AutoApproved."
                    ),
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    'request_type': self.leave_type.name,
                },
                email_template_model=LvEmailTemplate,
                notification_model= LvApprovalNotify,
            )

            return

        # ---------------- REPORTING MANAGER ---------------- #
        if approval_type == 'reporting_manager':

            manager = self.employee.emp_reporting_manager

            # ✅ FIX: convert to CustomUser
            if manager and hasattr(manager, 'user'):
                manager = manager.user

            if not manager:
                self.status = 'approved'
                self.save()

                send_notification_email(
                    user=self.created_by,
                    employee=self.employee,
                    branch=self.branch,
                    title="Request Approved",
                    notification_type="lv_request",
                    message=(f"Your LeaveRequest {self.leave_type}"
                        f"(Document No: {self.document_number}) has been Approved by ReportingManager."
                    ),
                    template_type="request_approved",
                    context={
                        **get_employee_context(self.employee),
                        'request_type': self.leave_type.name,
                    },
                    email_template_model=LvEmailTemplate,
                    notification_model= LvApprovalNotify,
                )

                return

            if not self.approvals.filter(level=1).exists():
                LeaveApproval.objects.create(
                    leave_request=self,
                    approver=manager,
                    level=1,
                    status=LeaveApproval.PENDING,
                    employee_id=self.employee.id
                )

                send_notification_email(
                    user=manager,
                    employee=self.employee,
                    branch=self.branch,
                    title="Request Created",
                    notification_type="lv_request",
                    message=(f"Your LeaveRequest {self.leave_type}"
                        f"(Document No: {self.document_number}) is waiting for your Approval."
                    ),
                    template_type="request_created",
                    context={
                        **get_employee_context(self.employee),
                        'request_type': self.leave_type.name,
                    },
                    email_template_model=LvEmailTemplate,
                    notification_model= LvApprovalNotify,
                )

            if self.approvals.filter(level=1, status=LeaveApproval.APPROVED).exists():
                self.status = 'approved'
                self.save()

                send_notification_email(
                    user=self.created_by,
                    branch=self.branch,
                    title="Request Approved",
                    notification_type="lv_request",
                    employee=self.employee,
                    message=(f"Your LeaveRequest {self.leave_type}"
                        f"(Document No: {self.document_number}) has been Approved by ReportingManager."
                    ),
                    template_type="request_approved",
                    context={
                        **get_employee_context(self.employee),
                        'request_type': self.leave_type.name,
                    },
                    email_template_model=LvEmailTemplate,
                    notification_model= LvApprovalNotify,
                )

            return

        # ---------------- MULTI APPROVAL ---------------- #

        last_approved = self.approvals.filter(
            status=LeaveApproval.APPROVED
        ).order_by('-level').first()

        current_level = (last_approved.level + 1) if last_approved else 1

        if self.approvals.filter(level=current_level).exists():
            return

        next_level = workflow.leave_levels.filter(level=current_level).first()

        if next_level:

            approver = next_level.approver

            # ✅ SAFETY (avoid NULL crash)
            if not approver:
                self.status = 'approved'
                self.save()

                send_notification_email(
                    user=self.created_by,
                    employee=self.employee,
                    branch=self.branch,
                    title="Request Approved",
                    notification_type="lv_request",
                    message=(f"Your LeaveRequest {self.leave_type}"
                        f"(Document No: {self.document_number}) has been Approved."
                    ),
                    template_type="request_approved",
                    context={
                        **get_employee_context(self.employee),
                        'request_type': self.leave_type.name,
                    },
                    email_template_model=LvEmailTemplate,
                    notification_model= LvApprovalNotify,
                )

                return

            LeaveApproval.objects.create(
                leave_request=self,
                approver=approver,
                level=next_level.level,
                status=LeaveApproval.PENDING,
                employee_id=self.employee.id
            )

            send_notification_email(
                user=approver,
                employee=self.employee,
                branch=self.branch,
                title="Request Created",
                notification_type="lv_request",
                message=(f"Your LeaveRequest {self.leave_type}"
                        f"(Document No: {self.document_number}) is requires your Approval."
                    ),
                template_type="request_created",
                context={
                    **get_employee_context(self.employee),
                    'request_type': self.leave_type.name,
                },
                email_template_model=LvEmailTemplate,
                notification_model= LvApprovalNotify,
            )

        else:
            self.status = 'approved'
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Request Approved",
                notification_type="lv_request",
                message=(f"Your LeaveRequest {self.leave_type}"
                        f"(Document No: {self.document_number}) has been fully Approved."
                    ),
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    'request_type': self.leave_type.name,
                },
                email_template_model=LvEmailTemplate,
                notification_model= LvApprovalNotify,
            )
   
class EmployeeRejoining(models.Model):
    employee           = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE)
    leave_request      = models.OneToOneField('employee_leave_request', on_delete=models.CASCADE)
    rejoining_date     = models.DateField()
    unpaid_leave_days  = models.FloatField(default=0)
    deduct_from_leave_type = models.ForeignKey(leave_type, on_delete=models.SET_NULL, null=True, blank=True)  
    deducted = models.BooleanField(default=False)  # <-- New field to track if deduction is already done
    created_at         = models.DateTimeField(auto_now_add=True)
    created_by         = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    
    def __str__(self):
        return f"Rejoining for {self.employee.emp_first_name} on {self.rejoining_date}"   
class LvRejectionReason(models.Model):
    reason_text = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.reason_text

class LVApprovalWorkflow(models.Model):
    APPROVAL_TYPE_CHOICES = [
        ('no_approval', 'No Approval'),
        ('reporting_manager', 'Reporting Manager'),
        ('multi_approval', 'Multi Approval'),
    ]

    request_type = models.ForeignKey(leave_type,related_name='lvapproval_workflows',on_delete=models.CASCADE)
    branch = models.ManyToManyField('OrganisationManager.brnch_mstr', blank=True)
    approval_type = models.CharField(max_length=30,choices=APPROVAL_TYPE_CHOICES,default='no_approval')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,related_name='lv_workflows_created')

    def __str__(self):
        return f"Workflow for {self.request_type.name}" 

class LeaveApprovalLevels(models.Model):
    workflow = models.ForeignKey(LVApprovalWorkflow,related_name='leave_levels',on_delete=models.CASCADE,null=True)
    level = models.IntegerField()
    approver = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,blank=True)
    is_compensatory = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,related_name='leave_levels_created')

    escalate_to = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,blank=True,related_name='lv_escalated_levels')
    escalate_after_days = models.PositiveIntegerField(default=0, help_text="Escalate after X days if pending")
    escalate_after_hours = models.PositiveIntegerField(default=0, help_text="Escalate after X hours if pending")
    escalate_after_minutes = models.PositiveIntegerField(default=0, help_text="Escalate after X minutes if pending")
    class Meta:
        permissions = (
                    ("add_leave_escalation", "Can add Escalation"),
                    ("view_leave_escalation", "Can view Escalation"),
                    ("change_leave_escalation", "Can change Escalation"),
                    ("export_leave_escalation", "Can export Escalation"),
                    ("delete_leave_escalation", "Can delete Escalation"),
            )
    def get_escalation_timedelta(self):
        """Returns the total time delta for escalation."""
        from datetime import timedelta
        total_minutes = (self.escalate_after_days * 24 * 60) + (self.escalate_after_hours * 60) + self.escalate_after_minutes
        return timedelta(minutes=total_minutes)


class LeaveApproval(models.Model):
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
    leave_request        = models.ForeignKey(employee_leave_request, related_name='approvals', on_delete=models.CASCADE,null=True, blank=True)
    compensatory_request = models.ForeignKey(CompensatoryLeaveRequest, related_name='approvals', on_delete=models.CASCADE, null=True, blank=True)
    approver             = models.ForeignKey('UserManagement.CustomUser', on_delete=models.CASCADE)
    # role                 = models.CharField(max_length=50, null=True, blank=True)
    level                = models.IntegerField(default=1)
    status               = models.CharField(max_length=20, choices=STATUS_CHOICES,default=PENDING)
    note                 = models.TextField(null=True, blank=True)
    # rejection_reason     = models.ForeignKey(LvRejectionReason,null=True, blank=True, on_delete=models.SET_NULL)
    rejection_reason     = models.TextField(null=True, blank=True)
    approved_days = models.FloatField(blank=True, null=True)  # ✅ NEW FIELD
    deligate_to     = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,blank=True,related_name='leave_deligations_received')
    is_deligate     = models.BooleanField(default=False)
    deligate_response = models.TextField(null=True, blank=True)
    created_at           = models.DateField(auto_now_add=True)
    created_by           = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    updated_at           = models.DateField(auto_now=True)
    employee_id          = models.IntegerField(null=True, blank=True)
    escalated = models.BooleanField(default=False)
    escalated_at = models.DateTimeField(null=True, blank=True)
    is_escalation = models.BooleanField(default=False)

    def approve(self, note=None, approved_days=None):
        self.status = self.APPROVED
        if note:
            self.note = note
        if approved_days is not None:
            self.approved_days = approved_days
            if self.leave_request:
                if approved_days > (self.leave_request.applied_days or self.leave_request.number_of_days):
                    raise ValueError("Approved days cannot exceed requested days")
                
                self.leave_request.approved_days = approved_days
                self.leave_request.status = (
                    'approved' if approved_days == (self.leave_request.applied_days or self.leave_request.number_of_days)
                    else 'approved'
                )
                self.leave_request.save()
        self.save()

        # Continue workflow
        if self.leave_request:
            self.leave_request.move_to_next_level()
        elif self.compensatory_request:
            self.compensatory_request.move_to_next_level()
    
    def reject(self, rejection_reason, note=None):
        if rejection_reason:
            self.rejection_reason = rejection_reason
        self.status = self.REJECTED
        if note:
            self.note = note
        self.save()
        if self.leave_request:
            self.leave_request.status = 'rejected'
            self.leave_request.save()
        elif self.compensatory_request:
            self.compensatory_request.status = 'rejected'
            self.compensatory_request.save()

        if self.leave_request:
            self.leave_request.status = 'rejected'
            self.leave_request.save()

            notification = LvApprovalNotify.objects.create(
                recipient_user=self.leave_request.created_by,
                message=(f"Your LeaveRequest {self.leave_type}"
                        f"(Document No: {self.document_number}) has been Rejected."
                    ),
            )
            notification.send_email_notification('request_rejected', {
                'leave_type': self.leave_request.leave_type,
                'start_date':self.leave_request.start_date,
                'end_date':self.leave_request.end_date,
                'status':self.leave_request.status,
                'document_number':self.leave_request.document_number,
                'rejection_reason': self.rejection_reason if self.rejection_reason else "No reason provided",
                'emp_gender': self.leave_request.employee.emp_gender,
                'emp_date_of_birth': self.leave_request.employee.emp_date_of_birth,
                'emp_personal_email': self.leave_request.employee.emp_personal_email,
                'emp_branch_name': self.leave_request.employee.emp_branch_id,
                'emp_department_name': self.leave_request.employee.emp_dept_id,
                'emp_designation_name': self.leave_request.employee.emp_desgntn_id,
                'emp_joined_date': self.leave_request.employee.emp_joined_date,
            })

            if self.leave_request.employee:
                notification = LvApprovalNotify.objects.create(
                    recipient_employee=self.leave_request.employee,
                    message=(f"Your LeaveRequest {self.leave_type}"
                        f"(Document No: {self.document_number}) has been Rejected."
                    ),
                )
                notification.send_email_notification('request_rejected', {
                    'leave_type': self.leave_request.leave_type,
                    'status':self.leave_request.status,
                    'start_date':self.leave_request.start_date,
                    'end_date':self.leave_request.end_date,
                    'document_number':self.leave_request.document_number,
                    'rejection_reason': self.rejection_reason if self.rejection_reason else "No reason provided",
                    'emp_gender': self.leave_request.employee.emp_gender,
                    'emp_date_of_birth': self.leave_request.employee.emp_date_of_birth,
                    'emp_personal_email': self.leave_request.employee.emp_personal_email,
                    'emp_branch_name': self.leave_request.employee.emp_branch_id,
                    'emp_department_name': self.leave_request.employee.emp_dept_id,
                    'emp_designation_name': self.leave_request.employee.emp_desgntn_id,
                    'emp_joined_date': self.leave_request.employee.emp_joined_date,
                })

        # Handle notifications for compensatory requests
        elif self.compensatory_request:
            self.compensatory_request.status = 'Rejected'
            self.compensatory_request.save()

            notification = LvApprovalNotify.objects.create(
                recipient_user=self.compensatory_request.created_by,
                message=f"Your compensatory leave request has been rejected."
            )
            notification.send_email_notification('request_rejected', {
                'request_type': 'Compensatory Leave',
                'rejection_reason': self.rejection_reason.reason_text if self.rejection_reason else "No reason provided",
                'emp_gender': self.compensatory_request.employee.emp_gender,
                'emp_date_of_birth': self.compensatory_request.employee.emp_date_of_birth,
                'emp_personal_email': self.compensatory_request.employee.emp_personal_email,
                'emp_branch_name': self.compensatory_request.employee.emp_branch_id,
                'emp_department_name': self.compensatory_request.employee.emp_dept_id,
                'emp_designation_name': self.compensatory_request.employee.emp_desgntn_id,
                'emp_joined_date': self.compensatory_request.employee.emp_joined_date,
            })

            if self.compensatory_request.employee:
                notification = LvApprovalNotify.objects.create(
                    recipient_employee=self.compensatory_request.employee,
                    message=f"Your compensatory leave request has been rejected."
                )
                notification.send_email_notification('request_rejected', {
                    'request_type': 'Compensatory Leave',
                    'rejection_reason': self.rejection_reason if self.rejection_reason else "No reason provided",
                    'emp_gender': self.compensatory_request.employee.emp_gender,
                    'emp_date_of_birth': self.compensatory_request.employee.emp_date_of_birth,
                    'emp_personal_email': self.compensatory_request.employee.emp_personal_email,
                    'emp_branch_name': self.compensatory_request.employee.emp_branch_id,
                    'emp_department_name': self.compensatory_request.employee.emp_dept_id,
                    'emp_designation_name': self.compensatory_request.employee.emp_desgntn_id,
                    'emp_joined_date': self.compensatory_request.employee.emp_joined_date,
                })
@receiver(post_save, sender=employee_leave_request)
def create_initial_leave_approval(sender, instance, created, **kwargs):
    if not created:
        return

    employee = instance.employee

    workflow = LVApprovalWorkflow.objects.filter(
        request_type=instance.leave_type,
        branch__in=[employee.emp_branch_id]
    ).first()

    if not workflow:
        instance.status = 'approved'
        instance.save(update_fields=['status'])

        send_notification_email(
            user=instance.created_by,
            employee=instance.employee,
            branch=instance.employee.emp_branch_id,
            title="Request Approved",
            notification_type="lv_request",
            message=(f"Your LeaveRequest {instance.leave_type}"
                    f"(Document No: {instance.document_number}) has been Approved."
                    ),
            template_type="request_approved",
            context={
                **get_employee_context(instance.employee),
                'request_type': instance.leave_type.name
            },
            email_template_model=LvEmailTemplate,
            notification_model= LvApprovalNotify,
        )

        return

    first_level = workflow.leave_levels.order_by('level').first()
    approval_type = workflow.approval_type

    # ---------------- NO APPROVAL ----------------
    if approval_type == 'no_approval':

        approver = instance.created_by

        if not approver and instance.employee and hasattr(instance.employee, 'users'):
            approver = instance.employee.users

        LeaveApproval.objects.create(
            leave_request=instance,
            approver=approver,
            level=1,
            status=LeaveApproval.APPROVED,
            # note="Auto Approved"
        )

        instance.status = 'approved'
        instance.save(update_fields=['status'])

        send_notification_email(
            user=approver,
            employee=instance.employee,
            branch=instance.employee.emp_branch_id,
            title="Request Approved",
            notification_type="lv_request",
            message=(f"Your LeaveRequest {instance.leave_type}"
                    f"(Document No: {instance.document_number}) has been AutoApproved."
                    ),
            template_type="request_approved",
            context={
                **get_employee_context(instance.employee),
                'request_type': instance.leave_type.name
            },
            email_template_model=LvEmailTemplate,
            notification_model= LvApprovalNotify,
        )

        return

    # ---------------- REPORTING MANAGER ----------------
    if approval_type == 'reporting_manager':

        manager = employee.emp_reporting_manager

        # ✅ FIX: convert manager → user
        if manager and hasattr(manager, 'user'):
            manager = manager.user

        if not manager:
            instance.status = 'approved'
            instance.save(update_fields=['status'])

            send_notification_email(
                user=instance.created_by,
                employee=instance.employee,
                branch=instance.employee.emp_branch_id,
                title="Request Approved",
                notification_type="lv_request",
                message=(f"Your LeaveRequest {instance.leave_type}"
                        f"(Document No: {instance.document_number}) has been Approved by ReportingManager."
                    ),
                template_type="request_approved",
                context={
                    **get_employee_context(instance.employee),
                    'request_type': instance.leave_type.name
                },
                email_template_model=LvEmailTemplate,
                notification_model= LvApprovalNotify
            )

            return

        LeaveApproval.objects.create(
            leave_request=instance,
            approver=manager,
            level=1,
            status=LeaveApproval.PENDING
        )

        send_notification_email(
            user=manager,
            employee=instance.employee,
            branch=instance.employee.emp_branch_id,
            title="Request Created",
            notification_type="lv_request",
            message=(f"Your LeaveRequest {instance.leave_type}"
                        f"(Document No: {instance.document_number}) is waiting for your Approval."
                    ),
            template_type="request_created",
            context={
                **get_employee_context(instance.employee),
                'request_type': instance.leave_type.name
            },
            email_template_model=LvEmailTemplate,
            notification_model= LvApprovalNotify,
        )

        return

    # ---------------- MULTI APPROVAL ----------------
    if approval_type == 'multi_approval':

        if not first_level:
            instance.status = 'approved'
            instance.save(update_fields=['status'])

            send_notification_email(
                user=instance.created_by,
                employee=instance.employee,
                branch=instance.employee.emp_branch_id,
                title="Request Approved",
                notification_type="lv_request",
                message=(f"Your LeaveRequest {instance.leave_type}"
                        f"(Document No: {instance.document_number}) has been Approved."
                    ),
                template_type="request_approved",
                context={
                    **get_employee_context(instance.employee),
                    'request_type': instance.leave_type.name
                },
                email_template_model=LvEmailTemplate,
                notification_model= LvApprovalNotify,
            )

            return

        approver = first_level.approver

        # ✅ FIX: fallback like general request
        if not approver:
            approver = instance.created_by

            if not approver and employee and hasattr(employee, 'user'):
                approver = employee.user

        if not approver:
            instance.status = 'approved'
            instance.save(update_fields=['status'])

            send_notification_email(
                user=instance.created_by,
                employee=instance.employee,
                branch=instance.employee.emp_branch_id,
                title="Request Approved",
                notification_type="lv_request",
                message=(f"Your LeaveRequest {instance.leave_type}"
                        f"(Document No: {instance.document_number}) has been Approved."
                    ),
                template_type="request_approved",
                context={
                    **get_employee_context(instance.employee),
                    'request_type': instance.leave_type.name
                },
                email_template_model=LvEmailTemplate,
                notification_model= LvApprovalNotify
            )

            return

        LeaveApproval.objects.create(
            leave_request=instance,
            approver=approver,
            level=first_level.level,
            status=LeaveApproval.PENDING
        )

        send_notification_email(
            user=approver,
            employee=instance.employee,
            branch=instance.employee.emp_branch_id,
            title="Request Created",
            notification_type="lv_request",
            message=(f"Your LeaveRequest {instance.leave_type}"
                        f"(Document No: {instance.document_number}) is requires your Approval."
                    ),
            template_type="request_created",
            context={
                **get_employee_context(instance.employee),
                'request_type': instance.leave_type.name
            },
            email_template_model=LvEmailTemplate,
            notification_model= LvApprovalNotify,
        )
        return

class EmployeeMachineMapping(models.Model):
    employee     = models.ForeignKey("EmpManagement.emp_master", on_delete=models.CASCADE)
    machine_code = models.CharField(max_length=100, unique=True)
    def __str__(self):
        return f'{self.employee.emp_code} - {self.machine_code}'

class Shift(models.Model):
    name           = models.CharField(max_length=50,unique=True)
    start_time     = models.TimeField(null=True, blank=True)  # Optional for off days
    end_time       = models.TimeField(null=True, blank=True)    # Optional for off days
    break_duration = models.DurationField(default=timedelta(minutes=0))  # Break time in minutes
    created_at     = models.DateTimeField(auto_now_add=True)
    created_by     = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    

    def __str__(self):
        return f"{self.name}"

class ShiftPattern(models.Model):
    """
    Defines a shift pattern (Zoho-style) with support for weekly and monthly rotation.

    Pattern Types:
      - 'weekly'  : Pattern repeats every N weeks. Each week in the cycle defines
                    per-day shift rules (Mon=Shift A, Tue=Shift B, etc.).
      - 'monthly' : Pattern repeats every N months. Each month in the cycle defines
                    date-range rules (Day 1-15 = Shift A, Day 16-Last = Shift B).

    `changes_every`:
      Number of weeks or months in one rotation cycle before it repeats.
      Example: changes_every=2 with weekly means Week 1 config, Week 2 config, then repeat.

    `pattern_config` (JSON):
      For 'monthly':
        {
          "months": [
            {
              "sequence": 1,
              "rules": [
                {"from": "1",  "to": "15",       "shift_id": <Shift.id>},
                {"from": "16", "to": "last_day", "shift_id": <Shift.id>}
              ]
            },
            {"sequence": 2, "rules": [...]}
          ]
        }

      For 'weekly':
        {
          "weeks": [
            {
              "sequence": 1,
              "rules": [
                {"day": "Monday",    "shift_id": <Shift.id>},
                {"day": "Tuesday",   "shift_id": <Shift.id>},
                ...
              ]
            },
            {"sequence": 2, "rules": [...]}
          ]
        }

    If `pattern_config` is empty, falls back to the per-weekday FK fields
    (monday_shift, tuesday_shift, etc.) for simple fixed schedules.
    """

    PATTERN_TYPES = (
        ("weekly",  "Weekly"),
        ("monthly", "Monthly"),
    )

    name          = models.CharField(max_length=100)
    pattern_type  = models.CharField(max_length=20, choices=PATTERN_TYPES, default="weekly")
    changes_every = models.PositiveIntegerField(
        default=1,
        help_text="Number of weeks/months in one rotation cycle before it repeats"
    )
    pattern_config = JSONField(
        default=dict,
        blank=True,
        null=True,
        help_text="JSON config defining shift rules per week/month sequence (see model docstring)"
    )

    # Fallback per-weekday shifts (used when pattern_config is empty)
    monday_shift    = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name='pattern_monday')
    tuesday_shift   = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name='pattern_tuesday')
    wednesday_shift = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name='pattern_wednesday')
    thursday_shift  = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name='pattern_thursday')
    friday_shift    = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name='pattern_friday')
    saturday_shift  = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name='pattern_saturday')
    sunday_shift    = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name='pattern_sunday')

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        'UserManagement.CustomUser', on_delete=models.SET_NULL, null=True,
        related_name='%(class)s_created_by'
    )

    def get_shift_for_date(self, target_date, start_date):
        """
        Resolve the applicable Shift for `target_date`, given the schedule's `start_date`.

        For 'monthly' pattern:
          1. Compute how many full months have elapsed since start_date.
          2. Determine the rotation sequence index: (months_elapsed % changes_every) + 1.
          3. Find the matching month config by sequence.
          4. Iterate rules; find which day-range the target day falls into.
          5. Return the Shift referenced by shift_id.

        For 'weekly' pattern:
          1. Compute how many full weeks have elapsed since start_date.
          2. Determine the rotation sequence index: (weeks_elapsed % changes_every) + 1.
          3. Find the matching week config by sequence.
          4. Match the target weekday name to a rule.
          5. Return the Shift referenced by shift_id.

        Falls back to get_shift_for_day() if no pattern_config is defined or no rule matches.
        """
        import calendar as cal

        if isinstance(target_date, datetime):
            target_date = target_date.date()
        if isinstance(start_date, datetime):
            start_date = start_date.date()

        if not self.pattern_config:
            return self.get_shift_for_day(target_date.weekday())

        if self.pattern_type == "monthly":
            months_elapsed = (
                (target_date.year - start_date.year) * 12
                + target_date.month - start_date.month
            )
            sequence_idx = (months_elapsed % self.changes_every) + 1
            months_config = self.pattern_config.get("months", [])
            seq_config = next(
                (m for m in months_config if m.get("sequence") == sequence_idx), None
            )
            if seq_config:
                day_num = target_date.day
                last_day = cal.monthrange(target_date.year, target_date.month)[1]

                # --- Option 1: day_map  {day_number_str: shift_id} -----------
                # Assigns a specific shift to every individual day of the month.
                # Example: {"1": 2, "2": 3, "3": 2, ..., "31": 1}
                day_map = seq_config.get("day_map")
                if day_map:
                    shift_id = day_map.get(str(day_num))
                    if shift_id:
                        try:
                            return Shift.objects.get(id=shift_id)
                        except Shift.DoesNotExist:
                            pass

                # --- Option 2: rules  (date-range based) ---------------------
                # Assigns shifts to ranges of days within the month.
                # Example: {"from": "1", "to": "15", "shift_id": 1}
                for rule in seq_config.get("rules", []):
                    from_str = str(rule.get("from", "1"))
                    to_str   = str(rule.get("to",   "last_day"))
                    from_val = int(from_str) if from_str.isdigit() else 1
                    to_val   = last_day if to_str.lower() in ("last_day", "last day") else int(to_str)
                    if from_val <= day_num <= to_val:
                        shift_id = rule.get("shift_id")
                        if shift_id:
                            try:
                                return Shift.objects.get(id=shift_id)
                            except Shift.DoesNotExist:
                                pass

        elif self.pattern_type == "weekly":
            days_elapsed  = (target_date - start_date).days
            weeks_elapsed = days_elapsed // 7
            sequence_idx  = (weeks_elapsed % self.changes_every) + 1
            weeks_config  = self.pattern_config.get("weeks", [])
            seq_config = next(
                (w for w in weeks_config if w.get("sequence") == sequence_idx), None
            )
            if seq_config:
                weekday_name = target_date.strftime("%A")
                for rule in seq_config.get("rules", []):
                    if rule.get("day") == weekday_name:
                        shift_id = rule.get("shift_id")
                        if shift_id:
                            try:
                                return Shift.objects.get(id=shift_id)
                            except Shift.DoesNotExist:
                                pass

        # Fallback: per-weekday FK fields
        return self.get_shift_for_day(target_date.weekday())

    def get_shift_for_day(self, weekday):
        """Return the fallback shift for the given weekday (0=Monday, ..., 6=Sunday)."""
        shifts = {
            0: self.monday_shift,
            1: self.tuesday_shift,
            2: self.wednesday_shift,
            3: self.thursday_shift,
            4: self.friday_shift,
            5: self.saturday_shift,
            6: self.sunday_shift,
        }
        return shifts.get(weekday)

    def __str__(self):
        return f"Shift Pattern: {self.name}"

class EmployeeShiftSchedule(models.Model):
    """
    Assigns a ShiftPattern to a group of employees for a given date range.

    The actual shift resolution logic (weekly / monthly rotation) lives entirely
    inside the linked ShiftPattern.  This model is just the assignment record.

    Assignable by: individual employees, departments, branches, designations, categories.
    """

    schedule_name = models.CharField(max_length=100, null=True, blank=True)

    # --- Who gets this schedule -------------------------------------------
    employee     = models.ManyToManyField(
        'EmpManagement.emp_master', blank=True, related_name="shift_schedules"
    )
    departments  = models.ManyToManyField(
        'OrganisationManager.dept_master', blank=True, related_name="shift_schedules"
    )
    branches     = models.ManyToManyField(
        'OrganisationManager.brnch_mstr', blank=True, related_name="shift_schedules"
    )
    designations = models.ManyToManyField(
        'OrganisationManager.desgntn_master', blank=True, related_name="shift_schedules"
    )
    categories   = models.ManyToManyField(
        'OrganisationManager.ctgry_master', blank=True, related_name="shift_schedules"
    )

    # --- When & which pattern -----------------------------------------------
    start_date    = models.DateField(default=timezone.now, help_text="Date from which this schedule is active")
    end_date      = models.DateField(null=True, blank=True, help_text="Date until which this schedule is active (inclusive); leave blank for open-ended")
    shift_pattern = models.ForeignKey(
        ShiftPattern, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='schedules'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        'UserManagement.CustomUser', on_delete=models.SET_NULL, null=True
    ) 

    # -----------------------------------------------------------------------
    def get_assigned_employees(self):
        """Return a distinct queryset of all employees covered by this schedule."""
        from EmpManagement.models import emp_master
        qs = emp_master.objects.none()

        if self.employee.exists():
            qs |= emp_master.objects.filter(id__in=self.employee.values_list('id', flat=True))
        if self.categories.exists():
            qs |= emp_master.objects.filter(emp_ctgry_id__in=self.categories.all())
        if self.departments.exists():
            qs |= emp_master.objects.filter(emp_dept_id__in=self.departments.all())
        if self.branches.exists():
            qs |= emp_master.objects.filter(emp_branch_id__in=self.branches.all())
        if self.designations.exists():
            qs |= emp_master.objects.filter(emp_desgntn_id__in=self.designations.all())

        return qs.distinct()

    def get_shift_for_date(self, date):
        """
        Delegates shift resolution to the linked ShiftPattern.
        Passes this schedule's start_date as the rotation anchor.
        Returns None if no pattern is assigned or the date is out of range.
        """
        if isinstance(date, datetime):
            date = date.date()

        start_date = self.start_date.date() if isinstance(self.start_date, datetime) else self.start_date
        end_date   = self.end_date
        if end_date and isinstance(end_date, datetime):
            end_date = end_date.date()

        # Enforce schedule date bounds
        if date < start_date:
            return None
        if end_date and date > end_date:
            return None

        if self.shift_pattern:
            return self.shift_pattern.get_shift_for_date(date, start_date)

        return None

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.end_date and self.start_date > self.end_date:
            raise ValidationError({
                "end_date": "End date must be greater than or equal to start date"
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Shift Schedule: {self.schedule_name}"

class ShiftOverride(models.Model):
    employee       = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE)
    date           = models.DateField()
    override_shift = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    created_by     = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    class Meta:
        unique_together = ('employee', 'date')  # Ensure only one override per employee per date

    def __str__(self):
        return f"Shift Override for {self.employee} on {self.date}"

class AttendancePolicy(models.Model):

    name = models.CharField(max_length=100)
    branch = models.ForeignKey(
        "OrganisationManager.brnch_mstr",
        on_delete=models.CASCADE,
        related_name="attendance_policies"
    )

    is_active = models.BooleanField(default=True)

    # Round Off
    round_off = models.BooleanField(default=False)

    # Check-in rules
    early_check_in = models.BooleanField(default=False)
    early_check_in_minutes = models.IntegerField(default=15)

    late_check_in = models.BooleanField(default=False)
    late_check_in_minutes = models.IntegerField(default=15)

    # Check-out rules
    early_check_out = models.BooleanField(default=False)
    early_check_out_minutes = models.IntegerField(default=15)

    late_check_out = models.BooleanField(default=False)
    late_check_out_minutes = models.IntegerField(default=15)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.branch.branch_name}"    

class AttendanceValidationPolicy(models.Model):
    name = models.CharField(max_length=100)
    # related_to = models.CharField(max_length=20, choices=EMP_CHOICES, default="company")
    
    # Assignments
    branch = models.ManyToManyField('OrganisationManager.brnch_mstr', blank=True)
    department = models.ManyToManyField('OrganisationManager.dept_master', blank=True)
    category = models.ManyToManyField('OrganisationManager.ctgry_master', blank=True)
    employee = models.ManyToManyField('EmpManagement.emp_master', blank=True)
    designation = models.ManyToManyField('OrganisationManager.desgntn_master', blank=True)
    
    # Validation Rules
    enable_geofencing = models.BooleanField(default=False)
    enable_face_recognition = models.BooleanField(default=False)
    enable_barcode_verification = models.BooleanField(default=False)
    enable_photo_capture = models.BooleanField(default=False)
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        'UserManagement.CustomUser', 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='%(class)s_created_by'
    )
    def __str__(self):
        return f"{self.name}"    

class Attendance(models.Model):
    employee        = models.ForeignKey("EmpManagement.emp_master", on_delete=models.CASCADE)
    shift           = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True)
    date            = models.DateField()
    check_in_time   = models.TimeField(null=True, blank=True)

    check_in_lat    = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    check_in_lng    = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    check_out_time  = models.TimeField(null=True, blank=True)
    
    check_out_lat   = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    check_out_lng   = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    check_in_location = models.CharField(max_length=255, null=True, blank=True)
    check_out_location = models.CharField(max_length=255, null=True, blank=True)    
    total_hours     = models.DurationField(null=True, blank=True)
    check_in_image = models.ImageField(upload_to="attendance/checkin/", null=True, blank=True)
    check_out_image = models.ImageField(upload_to="attendance/checkout/", null=True, blank=True)
    is_compensated  = models.BooleanField(default=False)    
    created_at      = models.DateTimeField(auto_now_add=True)
    created_by      = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    class Meta:
        unique_together = ('employee', 'date')
        permissions = (
                ("add_attendance_list", "Can add attendance list"),
                ("view_attendance_list", "Can attendance list"),
                ("export_attendance_list", "Can export attendance list"),
                ("delete_attendance_list", "Can delete attendance list"),
                ("change_attendance_list", "Can change attendance list"),
                
                
                ("add_early_going", "Can add early going"),
                ("view_early_going", "Can early going"),
                ("export_early_going", "Can early going"),
                ("delete_early_going", "Can delete early going"),
                ("change_early_going", "Can change early going"),

                ("add_attendance_manual", "Can  add attendance manual"),
                ("view_attendance_manual", "Can  list attendance manual"),
                ("export_attendance_manual", "Can export attendance manual"),
                ("delete_attendance_manual", "Can delete attendance manual"),
                ("change_attendance_manual", "Can change attendance manual"),

                ("add_attendance_faceregister", "Can  add attendance face register"),
                ("view_attendance_faceregister", "Can  list attendance face register"),
                ("export_attendance_faceregister", "Can export attendance face register"),
                ("delete_attendance_faceregister", "Can delete attendance face register"),
                ("change_attendance_faceregister", "Can change attendance face register"),
                ("import_attendance","Can import attendance")
        )
    def __str__(self):
        return f"attendance {self.employee} on {self.date}"
    def calculate_total_hours(self):
        if not (self.check_in_time and self.check_out_time):
            self.total_hours = None
            return

        start = datetime.combine(self.date, self.check_in_time)
        end = datetime.combine(self.date, self.check_out_time)

        if end < start:
            end += timedelta(days=1)

        self.total_hours = end - start
    def fetch_shift(self):
        from calendars.models import EmployeeShiftSchedule

        schedules = EmployeeShiftSchedule.objects.filter(
            start_date__lte=self.date
        ).filter(
            Q(end_date__gte=self.date) | Q(end_date__isnull=True)
        ).order_by("-start_date")

        for schedule in schedules:
            if schedule.get_assigned_employees().filter(id=self.employee.id).exists():
                return schedule.get_shift_for_date(self.date)

        return None
    # def fetch_shift(self):
    #     from calendars.models import EmployeeShiftSchedule

    #     schedule = EmployeeShiftSchedule.objects.filter(
    #         employee=self.employee,
    #         start_date__lte=self.date
    #     ).filter(
    #         Q(end_date__gte=self.date) | Q(end_date__isnull=True)
    #     ).order_by("-start_date").first()

    #     return schedule.get_shift_for_date(self.date) if schedule else None

    def get_shift_duration(self):
        if not self.shift:
            return timedelta(0)

        start = datetime.combine(self.date, self.shift.start_time)
        end = datetime.combine(self.date, self.shift.end_time)

        if end < start:
            end += timedelta(days=1)

        return end - start

    def is_weekend(self):
        from calendars.utils import get_employee_weekend_days
        return self.date.strftime("%A") in get_employee_weekend_days(self.employee)

    def is_holiday(self):
        from calendars.utils import get_employee_holidays
        return self.date in get_employee_holidays(self.employee, self.date, self.date)

    def save(self, *args, **kwargs):
        if self.check_in_time and self.check_out_time:
            self.calculate_total_hours()

        if not self.shift:
            self.shift = self.fetch_shift()

        super().save(*args, **kwargs)
        from calendars.utils import calculate_employee_overtime
        calculate_employee_overtime(self)

        # OT applicable?
        if not (self.employee.emp_ot_applicable and self.total_hours):
            return

        ot_type = self.get_ot_type()

        # Weekend or Holiday → full hours
        if ot_type in ["WEEKEND", "HOLIDAY"]:
            extra_duration = self.total_hours
        else:
            if not self.shift:
                return
            shift_duration = self.get_shift_duration()
            extra_duration = self.total_hours - shift_duration

        if extra_duration <= timedelta(0):
            return

        ot_hours = Decimal(extra_duration.total_seconds()) / Decimal(3600)

        from calendars.models import EmployeeOvertime

        EmployeeOvertime.objects.update_or_create(
            employee=self.employee,
            date=self.date,
            ot_type=ot_type,
            defaults={
                "hours": ot_hours.quantize(Decimal("0.01")),
                "approved": False,
                "created_by": self.created_by
            }
        )
    # # calendars/models.py (inside Attendance)

    def is_weekend(self):
        from .utils import get_employee_weekend_days
        return self.date.strftime("%A") in get_employee_weekend_days(self.employee)

    def is_holiday(self):
        from .utils import get_employee_holidays
        holidays = get_employee_holidays(self.employee, self.date, self.date)
        return self.date in holidays

    def get_ot_type(self):
        if self.is_holiday():
            return 'HOLIDAY'
        if self.is_weekend():
            return 'WEEKEND'
        return 'NORMAL'
@receiver(post_save, sender=Attendance)
def handle_rejoining(sender, instance, **kwargs):
    from .models import employee_leave_request, EmployeeRejoining

    employee = instance.employee

    # Make sure attendance_date is a date object
    attendance_date = instance.date
    if isinstance(attendance_date, datetime):
        attendance_date = attendance_date.date()

    leave_requests = employee_leave_request.objects.filter(
        employee=employee,
        status='approved',
        end_date__lt=attendance_date,
        employeerejoining__isnull=True
    ).order_by('end_date')

    if not leave_requests.exists():
        return

    leave_request = leave_requests.first()

    # Make sure end_date is a date object
    end_date = leave_request.end_date
    if isinstance(end_date, datetime):
        end_date = end_date.date()

    unpaid_days = max(0, (attendance_date - end_date).days - 1)

    EmployeeRejoining.objects.get_or_create(
        employee=employee,
        leave_request=leave_request,
        defaults={
            'rejoining_date': attendance_date,
            'unpaid_leave_days': unpaid_days,
        }
    )
@receiver(post_save, sender=Attendance)
def create_compensatory_record(sender, instance, created, **kwargs):

    if not created:
        return

    if instance.is_compensated:
        return

    if not (instance.is_weekend() or instance.is_holiday()):
        return

    if not instance.total_hours:
        return

    existing = CompensatoryLeaveAllocation.objects.filter(
        employee=instance.employee,
        attendances=instance
    ).exists()

    if existing:
        return

    allocation = CompensatoryLeaveAllocation.objects.create(
        employee=instance.employee,
        credited_days=1,
        reason=f"Worked on {instance.date}",
        created_by=instance.created_by
    )

    allocation.attendances.add(instance)
class AttendanceLog(models.Model):
    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE, related_name='logs')
    log_type = models.CharField(max_length=10, choices=(('check_in', 'Check In'), ('check_out', 'Check Out')))
    timestamp = models.DateTimeField(auto_now_add=True)
    lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    location = models.CharField(max_length=255, null=True, blank=True)
    is_face_verified = models.BooleanField(default=False)
    verification_photo = models.ImageField(upload_to='attendance_verification/', null=True, blank=True)
    auth_method = models.CharField(
        max_length=20, 
        choices=(('face', 'Face Recognition'), ('barcode', 'Barcode Scan'), ('manual', 'Manual Entry')),
        default='manual'
    )
    def __str__(self):
        return f"{self.attendance.employee} - {self.log_type} at {self.timestamp}"
    
class AttendanceRecheck(models.Model):
    attendance = models.ForeignKey(Attendance,on_delete=models.CASCADE,related_name='rechecks')
    checked_at = models.DateTimeField(auto_now_add=True)
    lat = models.DecimalField(max_digits=9, decimal_places=6)
    lng = models.DecimalField(max_digits=9, decimal_places=6)
    location = models.CharField(max_length=255)

    requested_by = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,related_name='attendance_rechecks')

    class Meta:
        ordering = ['-checked_at']
    def __str__(self):
        return f"{self.attendance} " 
    

class LatinEarlyoutEmailTemplate(models.Model):
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
   
    def __str__(self):
        return f"{self.template_type} - {self.subject}"
    
class LateinEarlyRequestNotification(models.Model):
    recipient_user     = models.ForeignKey('UserManagement.CustomUser',null=True,blank=True,on_delete=models.CASCADE,related_name='late_early_notifications')
    recipient_employee = models.ForeignKey(emp_master,null=True,blank=True,on_delete=models.CASCADE,related_name='late_early_notifications')
    message            = models.CharField(max_length=255)
    created_at         = models.DateTimeField(auto_now_add=True)
    is_read            = models.BooleanField(default=False)
    deligate_user      = models.ForeignKey('UserManagement.CustomUser',null=True,blank=True,on_delete=models.CASCADE,related_name='lateinearlyout_deligated_notifications')
    
    def __str__(self):
        if self.recipient_user:
            return f"Notification for {self.recipient_user.emp_code}: {self.message}"
        else:
            return f"Notification for employee: {self.message}"
    
class LateinEarlyoutRequest(models.Model):
    REQUEST_TYPE_CHOICES = (
        ('LATE_IN', 'Late Check In'),
        ('EARLY_OUT', 'Early Check Out'),
    )

    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    )
    document_number  = models.CharField(max_length=50, unique=True, blank=True)
    date         = models.DateField(null=True, blank=True)
    employee     = models.ForeignKey("EmpManagement.emp_master",on_delete=models.CASCADE)
    request_type = models.CharField(max_length=20, choices=REQUEST_TYPE_CHOICES)
    branch       = models.ForeignKey('OrganisationManager.brnch_mstr', on_delete=models.CASCADE, null=True, blank=True, related_name='lateinearlyoutreq')
    reason       = models.TextField(null=True, blank=True)
    status       = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at   = models.DateTimeField(auto_now_add=True)
    created_by   = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True)

    def __str__(self):
        return f"{self.document_number} - {self.employee} - {self.request_type} - {self.status}"

    def move_to_next_level(self):
        workflow = LatinEarlyApprovalWorkflow.objects.filter(
            branch=self.employee.emp_branch_id
            ).first()
        if not workflow:
            return
        approval_type = workflow.approval_type

        # =========================
        # REJECT CHECK (GLOBAL)
        # =========================
        if self.lateinearlyout_approvals.filter(status=LateinEarlyoutApproval.REJECTED).exists():  # ✅ FIX constant
            self.status = 'REJECTED'
            self.save()

            send_notification_email(
                employee=self.employee,
                message=f"Your LateinEarlyoutRequest {self.request_type} has been Rejected.",
                template_type="request_rejected",
                context={
                    **get_employee_context(self.employee),
                    'request_type': self.request_type,
                    'reason': self.reason,
                    'status': self.status,
                },
                email_template_model=LatinEarlyoutEmailTemplate,
                notification_model=LateinEarlyRequestNotification
            )
            return
        
        # =========================
        # NO APPROVAL
        # =========================
        
        if approval_type == "no_approval":
            self.status = "APPROVED"
            self.save()
            send_notification_email(
            employee=self.employee,
            message=f"Your LateinEarlyoutRequest {self.request_type} has been AutoApproved.",
            template_type="request_approved",
            context={
                **get_employee_context(self.employee),
                'request_type': self.request_type,
                'reason': self.reason,
                'status': self.status,
            },
            email_template_model=LatinEarlyoutEmailTemplate,
            notification_model=LateinEarlyRequestNotification
            )
            return
    
        # =========================
        # REPORTING MANAGER
        # =========================
        
        if approval_type == "reporting_manager":
            if self.lateinearlyout_approvals.filter(status="APPROVED").exists():
                self.status = "APPROVED"
                self.save()
                send_notification_email(
                employee=self.employee,
                message=f"Your LateinEarlyout Request {self.request_type} has been Approved by ReportingManager.",
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    'request_type': self.request_type,
                    'reason': self.reason,
                    'status': self.status,
                },
                email_template_model=LatinEarlyoutEmailTemplate,
                notification_model=LateinEarlyRequestNotification
                )
                return

        # =========================
        # MULTI APPROVAL 
        # =========================

        last_approved = self.lateinearlyout_approvals.filter(
            status=LateinEarlyoutApproval.APPROVED
        ).order_by('-level').first()

        current_level = (last_approved.level + 1) if last_approved else 1

        # prevent duplicate approval at same level
        if self.lateinearlyout_approvals.filter(
            level=current_level,
            status=LateinEarlyoutApproval.PENDING
        ).exists():
            return

        next_level = workflow.lateinearlyout_levels.filter(
            level=current_level
        ).first()

        if next_level and next_level.approver:

            LateinEarlyoutApproval.objects.create(
                lateinearlyout_request=self,
                approver=next_level.approver,
                role=next_level.role,
                level=next_level.level,
                status=LateinEarlyoutApproval.PENDING
            )

            send_notification_email(
                user=next_level.approver,
                employee=None,
                message=f"Your LateinEarlyout Request {self.request_type} is waiting for your Approval.",
                template_type="request_created",
                context={
                    **get_employee_context(self.employee),
                    'request_type': self.request_type,
                    'reason': self.reason,
                },
                email_template_model=LatinEarlyoutEmailTemplate,
                notification_model=LateinEarlyRequestNotification
            )

        else:
            self.status = "APPROVED"
            self.save()

            send_notification_email(
                employee=self.employee,
                message=f"Your LateinEarlyout Request {self.request_type} has been fully Approved.",
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    'request_type': self.request_type,
                    'reason': self.reason,
                    'status': self.status,
                },
                email_template_model=LatinEarlyoutEmailTemplate,
                notification_model=LateinEarlyRequestNotification
                )

    
class LatinEarlyApprovalWorkflow(models.Model):
    APPROVAL_TYPE_CHOICES = [
        ('no_approval', 'No Approval'),
        ('reporting_manager', 'Reporting Manager'),
        ('multi_approval', 'Multi Approval'),
    ]

    branch = models.ManyToManyField('OrganisationManager.brnch_mstr', blank=True)
    approval_type = models.CharField(max_length=30, choices=APPROVAL_TYPE_CHOICES, default='no_approval')

    def __str__(self):
        return f"Workflow ({self.approval_type})"
    
class LateinEarlyoutApprovalLevel(models.Model):

    workflow = models.ForeignKey('LatinEarlyApprovalWorkflow',related_name='lateinearlyout_levels',on_delete=models.CASCADE,null=True)
    level = models.PositiveIntegerField()
    approver = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,blank=True)
    role = models.CharField(max_length=100)

    class Meta:
        ordering = ['level']
        unique_together = ('workflow', 'level') 

    def __str__(self):
        approver_name = (
            self.approver.emp_code
            if self.approver and hasattr(self.approver, 'emp_code')
            else "No Approver"
        )
        return f"{self.workflow} - Level {self.level} - {self.role} ({approver_name})"
    
class LateinEarlyoutApproval(models.Model):
    PENDING = 'PENDING'
    APPROVED = 'APPROVED'
    REJECTED = 'REJECTED'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (APPROVED, 'Approved'),
        (REJECTED, 'Rejected'),
    ]

    lateinearlyout_request = models.ForeignKey(LateinEarlyoutRequest,related_name='lateinearlyout_approvals',on_delete=models.CASCADE)
    approver               = models.ForeignKey('UserManagement.CustomUser',on_delete=models.CASCADE,null=True)
    role                   = models.CharField(max_length=50, null=True, blank=True)
    level                  = models.IntegerField(default=1)
    status                 = models.CharField(max_length=20,choices=STATUS_CHOICES,default=PENDING)
    note                   = models.TextField(null=True, blank=True)
    deligate_to            = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,blank=True,related_name='lateinearlyout_deligations_received')
    is_deligate            = models.BooleanField(default=False)
    deligate_response      = models.TextField(null=True, blank=True)
    created_at             = models.DateTimeField(auto_now_add=True)
    created_by             = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,related_name='late_early_created_by')
    updated_at             = models.DateTimeField(auto_now=True)

    def approve(self, note=None):
        self.status = self.APPROVED
        if note:
            self.note = note
        self.save()

        # ✅ Move to next level
        self.lateinearlyout_request.move_to_next_level()

    def reject(self, note=None):
        self.status = self.REJECTED
        if note:
            self.note = note
        self.save()

        request = self.lateinearlyout_request
        request.status = 'REJECTED'
        request.save()

        send_notification_email(
            employee=request.employee,
            message=f"Your LateinEarlyoutRequest {self.request_type} has been Rejected.",
            template_type="request_rejected",
            context={
                **get_employee_context(request.employee),
                'request_type': request.request_type,
                'reason': request.reason,
                'status': request.status,
            },
            email_template_model=LatinEarlyoutEmailTemplate,
            notification_model=LateinEarlyRequestNotification
        )

@receiver(post_save, sender=LateinEarlyoutRequest)
def create_initial_approval(sender, instance, created, **kwargs):

    if not created:
        return

    with transaction.atomic():

        # ================= WORKFLOW =================
        workflow = LatinEarlyApprovalWorkflow.objects.filter(
            branch=instance.employee.emp_branch_id
        ).first()

        if not workflow:
            raise Exception("Approval workflow not configured for this branch.")

        approval_type = workflow.approval_type

        # ================= NO APPROVAL =================
        if approval_type == 'no_approval':

            LateinEarlyoutApproval.objects.create(
                lateinearlyout_request=instance,
                approver=instance.created_by,
                role="Auto Approval",
                level=1,
                status=LateinEarlyoutApproval.APPROVED
            )
            instance.status = "APPROVED"
            instance.save(update_fields=["status"])
            send_notification_email(
                employee=instance.employee,
                message=f"Your LateinEarlyoutRequest {instance.request_type} has been AutoApproved.",
                template_type="request_approved",
                context={
                    **get_employee_context(instance.employee),
                    'request_type': instance.request_type,
                    'status': instance.status,
                },
                email_template_model=LatinEarlyoutEmailTemplate,
                notification_model=LateinEarlyRequestNotification
            )
            return

        # ================= REPORTING MANAGER =================
        if approval_type == 'reporting_manager':

            manager = instance.employee.emp_reporting_manager

            if not manager:
                raise Exception("Employee has no reporting manager.")

            LateinEarlyoutApproval.objects.create(
                lateinearlyout_request=instance,
                approver=manager,
                role="Reporting Manager",
                level=1,
                status=LateinEarlyoutApproval.PENDING
            )

            send_notification_email(
                user=manager,
                employee=None,
                message=f"Your LateinEarlyoutRequest {instance.request_type} is waiting for your Approval.",
                template_type="request_created",
                context={
                    **get_employee_context(instance.employee),
                    'request_type': instance.request_type,
                },
                email_template_model=LatinEarlyoutEmailTemplate,
                notification_model=LateinEarlyRequestNotification
            )
            return

        # ================= MULTI APPROVAL =================
        first_level = workflow.lateinearlyout_levels.order_by('level').first()

        if not first_level:
            raise Exception("Approval levels not configured.")

        LateinEarlyoutApproval.objects.create(
            lateinearlyout_request=instance,
            approver=first_level.approver,
            role=first_level.role,
            level=first_level.level,
            status=LateinEarlyoutApproval.PENDING
        )
        send_notification_email(
            user=first_level.approver,
            employee=None,
            message=f"Your LateinEarlyoutRequest {instance.request_type} is waiting for your Approval.",
            template_type="request_created",
            context={
                **get_employee_context(instance.employee),
                'request_type': instance.request_type,
            },
            email_template_model=LatinEarlyoutEmailTemplate,
            notification_model=LateinEarlyRequestNotification
        )
class EmployeeOvertime(models.Model):

    OT_TYPE_CHOICES = (
        ('NORMAL', 'Normal OT'),
        ('WEEKEND', 'Weekend OT'),
        ('HOLIDAY', 'Holiday OT'),
    )
    SLAB_CHOICES = (
        ('OT', 'Overtime'),
        ('EXT', 'Extended Overtime'),
    )
    employee = models.ForeignKey(
        'EmpManagement.emp_master',
        on_delete=models.CASCADE
    )
    date = models.DateField()

    ot_type = models.CharField(
        max_length=10,
        choices=OT_TYPE_CHOICES,null=True, blank=True,
    )
    slab = models.CharField(
        max_length=5,
        choices=SLAB_CHOICES,
        default='OT'
    )
    hours = models.DecimalField(max_digits=6, decimal_places=2)

    approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        'UserManagement.CustomUser',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='approved_overtimes'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        'UserManagement.CustomUser',
        null=True,
        on_delete=models.SET_NULL,
        related_name='%(class)s_created_by'
    )

    class Meta:
        unique_together = ('employee', 'date', 'ot_type')

    def __str__(self):
        return f"{self.employee} - {self.ot_type} - {self.date}"


class OvertimePolicy(models.Model):

    OT_TYPE_CHOICES = (
        ('NORMAL', 'Normal OT'),
        ('WEEKEND', 'Weekend OT'),
        ('HOLIDAY', 'Holiday OT'),
    )
    name = models.CharField(max_length=100)
    ot_type = models.CharField(max_length=10,choices=OT_TYPE_CHOICES,null=True, blank=True,)
    rate_multiplier = models.DecimalField( max_digits=4, decimal_places=2,help_text="Example: 1.5, 2.0")
    # Applicability (ALL OPTIONAL)
    branch = models.ManyToManyField( 'OrganisationManager.brnch_mstr', null=True, blank=True)
    department = models.ManyToManyField('OrganisationManager.dept_master',null=True, blank=True)
    designation = models.ManyToManyField('OrganisationManager.desgntn_master',null=True, blank=True)
    category = models.ManyToManyField('OrganisationManager.ctgry_master',null=True, blank=True)

    # priority = models.PositiveIntegerField(
    #     default=1,
    #     help_text="Lower value = higher priority"
    # )

    is_active = models.BooleanField(default=True)

    # class Meta:
    #     ordering = ['priority']

    def __str__(self):
        return f"{self.name} ({self.ot_type})"
class OvertimeRule(models.Model):

    RULE_TYPE_CHOICES = (
        ('DAILY', 'Daily'),
        ('WEEKLY', 'Weekly'),
        ('MONTHLY', 'Monthly'),
    )

    BASE_CHOICES = (
        ('FIXED', 'Fixed Hours'),
        ('SHIFT', 'Shift Hours'),
    )

    policy = models.ForeignKey(
        OvertimePolicy,
        on_delete=models.CASCADE,
        related_name='rules'
    )

    rule_type = models.CharField(
        max_length=10,
        choices=RULE_TYPE_CHOICES,
        default='DAILY'
    )

    base_type = models.CharField(
        max_length=10,
        choices=BASE_CHOICES,
        default='SHIFT'
    )

    threshold_hours = models.DurationField(
        help_text="Hours after which OT applies"
    )

    is_extended = models.BooleanField(
        default=False,
        help_text="Extended overtime slab"
    )

    order = models.PositiveIntegerField(
        help_text="Execution order (1,2,3...)"
    )

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.policy.name} | {self.threshold_hours} | {'EXT' if self.is_extended else 'OT'}"


class LeaveReport(models.Model):
    file_name   = models.CharField(max_length=100,unique=True)
    report_data = models.FileField(upload_to='leave_report/', null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    created_by  = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    class Meta:
        permissions = (
            ('lv_export_report', 'Can export lv report'),
            # Add more custom permissions here
        )
    
    
    def __str__(self):
        return self.file_name 
    
class LeaveApprovalReport(models.Model):
    file_name   = models.CharField(max_length=100,unique=True)
    report_data = models.FileField(upload_to='leave_approval_report/', null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    created_by  = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')


    class Meta:
        permissions = (
            ('lv_approval_export_report', 'Can export lv approval report'),
            # Add more custom permissions here
        )
       
    def __str__(self):
        return self.file_name 


class AttendanceReport(models.Model):
    file_name   = models.CharField(max_length=100,unique=True)
    report_data = models.FileField(upload_to='attendance_report/', null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    created_by  = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    class Meta:
        permissions = (
            ('attendance_export_report', 'Can export attendance report'),
            # Add more custom permissions here
        )
       
    def __str__(self):
        return self.file_name 

class lvBalanceReport(models.Model):
    file_name   = models.CharField(max_length=100,unique=True)
    report_data = models.FileField(upload_to='lvbalance_report/', null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    created_by  = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    class Meta:
        permissions = (
            ('lv_balance_export_report', 'Can export lv balance report'),
            # Add more custom permissions here
        )
       
    def __str__(self):
        return self.file_name

    
    
    
class EmployeeYearlyCalendar(models.Model):
    emp        = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE, related_name='yearly_calendar')
    year       = models.PositiveIntegerField()
    # Store data for each day in a JSON format, for example: {"2024-01-01": {"status": "Holiday", "remarks": "New Year"}}
    daily_data = models.JSONField(default=dict)  # Stores the daily status, leave type, etc.
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('emp', 'year')
        ordering = ['year']

    def __str__(self):
        return f"Yearly Calendar for {self.emp} - {self.year}"
class AttendanceCalendar(models.Model):
    STATUS_CHOICES = [
        ('Present', 'Present'),
        ('Absent', 'Absent'),
        ('Leave', 'Leave'),
        ('Weekend', 'Weekend'),
        ('Holiday', 'Holiday'),
    ]
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE, related_name='attendance_calendar_entries')
    date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Absent')
    leave_type = models.ForeignKey('leave_type', on_delete=models.SET_NULL, null=True, blank=True)
    is_half_day = models.BooleanField(default=False)
    half_day_period = models.CharField(max_length=20, choices=[('first_half', 'First Half'), ('second_half', 'Second Half')], null=True, blank=True)
    unpaid_fraction = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    is_manual = models.BooleanField(default=False)
    remarks = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'date')
        ordering = ['date']
        verbose_name = "Attendance Calendar"
        verbose_name_plural = "Attendance Calendars"

    def __str__(self):
        return f"{self.employee.emp_first_name} - {self.date} - {self.status}"
    def populate_calendar(self, holidays, weekends, attendance, leave_requests):
        """
        Populate the calendar with holidays, weekends, attendance, and leave requests.
        """
        start_date = date(self.year, 1, 1)
        end_date = date(self.year, 12, 31)

        current_date = start_date
        while current_date <= end_date:
            # Set initial status
            day_status = 'Work'
            remarks = None
            leave_type = None

            # Check if it's a holiday
            if current_date in holidays:
                day_status = 'Holiday'
                remarks = 'Holiday'

            # Check if it's a weekend
            elif any(weekend.is_weekend(current_date) for weekend in weekends):
                day_status = 'Weekend'
                remarks = 'Weekend'

            # Check if leave is approved for the day
            elif any(l.start_date <= current_date <= l.end_date and l.status == 'Approved' for l in leave_requests):
                day_status = 'Leave'
                leave_type = next((l.leave_type.name for l in leave_requests if l.start_date <= current_date <= l.end_date and l.status == 'Approved'), None)
                remarks = f"Leave: {leave_type}"

            # Check attendance
            elif any(a.date == current_date for a in attendance):
                day_status = 'Present'
                remarks = 'Attended'

            # Populate the daily data
            self.daily_data[str(current_date)] = {
                'status': day_status,
                'remarks': remarks,
                'leave_type': leave_type
            }

            current_date += timedelta(days=1)

        self.save()

class MonthlyAttendanceSummary(models.Model):
    employee = models.ForeignKey(emp_master, on_delete=models.CASCADE)
    month = models.IntegerField()  # 1 to 12
    year = models.IntegerField()
    summary_data = models.JSONField()  # To store daily records (date, status, leave_type)
    total_present = models.IntegerField()
    total_absent = models.IntegerField()
    
    class Meta:
        unique_together = ('employee', 'month', 'year')  # Prevent duplicates
    
class LateComingPolicy(models.Model):

    PENALTY_CHOICES = [
        ('half_day', 'Half Day'),
        ('full_day', 'Full Day'),
        ('leave_deduction', 'Leave Deduction'),
    ]

    attendance_policy = models.OneToOneField(
        AttendancePolicy,
        on_delete=models.CASCADE,
        related_name="late_coming_policy"
    )

    enabled = models.BooleanField(default=False)

    late_occurrence_limit = models.PositiveIntegerField(
        default=3,
        help_text="Number of late arrivals before penalty"
    )

    penalty_type = models.CharField(
        max_length=20,
        choices=PENALTY_CHOICES,
        default='half_day'
    )

    leave_days_to_deduct = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=0.5
    )

    reset_monthly = models.BooleanField(default=True)
    # Assignments
    branch = models.ManyToManyField('OrganisationManager.brnch_mstr', blank=True)
    department = models.ManyToManyField('OrganisationManager.dept_master', blank=True)
    category = models.ManyToManyField('OrganisationManager.ctgry_master', blank=True)
    employee = models.ManyToManyField('EmpManagement.emp_master', blank=True)
    designation = models.ManyToManyField('OrganisationManager.desgntn_master', blank=True)

    def __str__(self):
        return f"{self.attendance_policy.name} - Late Coming"
    
class EarlyExitPolicy(models.Model):

    PENALTY_CHOICES = [
        ('half_day', 'Half Day'),
        ('full_day', 'Full Day'),
        ('leave_deduction', 'Leave Deduction'),
    ]

    attendance_policy = models.OneToOneField(
        AttendancePolicy,
        on_delete=models.CASCADE,
        related_name="early_exit_policy"
    )

    enabled = models.BooleanField(default=False)

    occurrence_limit = models.PositiveIntegerField(default=3)

    penalty_type = models.CharField(
        max_length=20,
        choices=PENALTY_CHOICES,
        default='half_day'
    )

    leave_days_to_deduct = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=0.5
    )

    reset_monthly = models.BooleanField(default=True)
    # Assignments
    branch = models.ManyToManyField('OrganisationManager.brnch_mstr', blank=True)
    department = models.ManyToManyField('OrganisationManager.dept_master', blank=True)
    category = models.ManyToManyField('OrganisationManager.ctgry_master', blank=True)
    employee = models.ManyToManyField('EmpManagement.emp_master', blank=True)
    designation = models.ManyToManyField('OrganisationManager.desgntn_master', blank=True)

    def __str__(self):
        return f"{self.attendance_policy.name} - Early Exit"




