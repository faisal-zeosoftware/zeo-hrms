from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.utils import timezone
from datetime import datetime,timedelta
from EmpManagement .utils import send_notification_email,get_employee_context
from EmpManagement .models import RequestNotification
# from calendars .models import LeaveApproval
from django.db.models import Max 
from django.db import transaction
from django.db.models.signals import pre_save
from django.conf import settings
from decimal import Decimal
# Create your models here.

    
class SalaryComponent(models.Model):
    COMPONENT_TYPES = [
        ('deduction', 'Deduction'),
        ('addition', 'Addition'),
        ('others', 'Others'),
    ]
    COMPONENT_MASTER_CHOICES = [
        ('basic', 'Basic Salary'),
        ('hra', 'HRA'),
        ('education_allowance', 'Education Allowance'),
        ('transport_allowance', 'Transport Allowance'),
        ('food_allowance', 'Food Allowance'),
        ('housing_allowance', 'Housing Allowance'),
        ('telephone_allowance', 'Telephone Allowance'),
        ('medical_allowance', 'Medical Allowance'),
        ('travel_allowance', 'Travel Allowance'),
        ('other_allowance', 'Other Allowance'),
        ('bonus', 'Bonus'),
        ('commission', 'Commission'),
        ('overtime', 'Overtime'),
        ('loan', 'Loan Deduction'),
        ('advance_salary', 'Advance Salary'),
        ('air_ticket', 'Air Ticket'),
        ('gratuity', 'Gratuity'),
        ('leave_encashment', 'Leave Encashment'),
        ('pf', 'Provident Fund'),
        ('esi', 'ESI'),
        ('tax', 'Tax'),
        
    ]
    COMPONENT_VALUE_TYPES = [
    ('fixed', 'Fixed'),
    ('variable', 'Variable'),]

    name = models.CharField(max_length=100)  # Component name (e.g., HRA, PF)
    component_type = models.CharField(max_length=20, choices=COMPONENT_TYPES)
    payroll_category = models.CharField(max_length=50,choices=COMPONENT_MASTER_CHOICES,default='basic')
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', on_delete=models.CASCADE,null=True,blank=True, related_name='salary_components')
    code = models.CharField(max_length=20,null=True)
    deduct_leave=models.BooleanField(default=False)
    component_value_type = models.CharField(max_length=20,choices=COMPONENT_VALUE_TYPES,default='fixed')
    formula = models.CharField(max_length=255, blank=True, null=True, help_text="Formula to calculate this component (e.g., 'basic_salary * 0.4')")
    description = models.TextField(blank=True, null=True)
    show_in_payslip = models.BooleanField(default=True, help_text="Should this component be shown on the payslip?")
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['name', 'branch'], name='unique_salary_component_name_per_branch'),
            models.UniqueConstraint(fields=['code', 'branch'], name='unique_salary_component_code_per_branch'),
        ]
    def __str__(self):
        return f"{self.name} ({self.get_component_type_display()})"

class SalaryStructure(models.Model):
    name = models.CharField(max_length=100, help_text="Name of the structure (e.g., Manager, Worker)")
    description = models.TextField(blank=True, null=True)
    branch = models.ManyToManyField('OrganisationManager.brnch_mstr',null=True, blank=True, related_name='salary_structures')
    components = models.ManyToManyField(SalaryComponent, related_name='salary_structures', help_text="Salary components included in this structure")
    employees = models.ManyToManyField('EmpManagement.emp_master', related_name='assigned_salary_structures', blank=True, help_text="Employees assigned to this structure")
    is_active = models.BooleanField(default=True)
    date_created = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
class EmployeeSalaryStructure(models.Model):
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE, related_name='salary_structures')
    component = models.ForeignKey(SalaryComponent, on_delete=models.CASCADE, related_name='employee_components')
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, null=True, blank=True, help_text="Amount for this component")
    is_active = models.BooleanField(default=True, help_text="Is this component active for the employee?")
    date_created = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)
    valid_from = models.DateField(
        null=True,
        blank=True,
        help_text="Month from which this amount becomes active"
    )

    valid_until = models.DateField(
        null=True,
        blank=True,
        help_text="Last month for which this amount is active"
    )

    class Meta:
        unique_together = ('employee', 'component')  # Ensure no duplicate components for an employee
        permissions = (
            ('import_salarycomponent', 'Can import Salary component'),
            # Add more custom permissions here
        )

    def __str__(self):
        return f"{self.employee} - {self.component.name} ({self.amount})"

class PayStructure(models.Model):

    WORKING_DAY_CHOICES = [
        ('SUN', 'Sunday'),
        ('MON', 'Monday'),
        ('TUE', 'Tuesday'),
        ('WED', 'Wednesday'),
        ('THU', 'Thursday'),
        ('FRI', 'Friday'),
        ('SAT', 'Saturday'),
    ]

    SALARY_CALCULATION_CHOICES = [
        ('CALENDAR_DAYS', 'Calendar Days'),
        ('ORGANIZATION_DAYS', 'Organization Days'),
        ('FIXED_DAYS', 'Fixed Days'),
    ]


    ATTENDANCE_CYCLE_CHOICES = [
        ('MONTH', 'Calendar Month'),
        ('CUSTOM', 'Custom Cycle'),
    ]

    branch = models.OneToOneField(
        'OrganisationManager.brnch_mstr',
        on_delete=models.CASCADE,
        related_name='pay_structure'
    )

    # 1️⃣ Working week
    working_days = models.JSONField(
        default=list,
        help_text="Example: ['MON','TUE','WED','THU','FRI']"
    )

    # 2️⃣ Salary calculation
    salary_calculation_type = models.CharField(
        max_length=20,
        choices=SALARY_CALCULATION_CHOICES,
        default='CALENDAR_DAYS'
    )
    fixed_working_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True
    )

    # 3️⃣ Attendance cycle
    attendance_cycle_type = models.CharField(
        max_length=10,
        choices=ATTENDANCE_CYCLE_CHOICES,
        default='MONTH'
    )
    cycle_start_day = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Example: 26"
    )
    cycle_end_day = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Example: 25"
    )

    payday = models.PositiveSmallIntegerField(
        null=True,
        blank=True
    )

    payroll_start_month = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"PayStructure - {self.branch}"

    def clean(self):
        super().clean()
        if self.salary_calculation_type in ['ORGANIZATION_DAYS', 'FIXED_DAYS'] and self.fixed_working_days is None:
            raise ValidationError({
                'fixed_working_days': f"Fixed working days is required when calculation type is {self.get_salary_calculation_type_display()}."
            })
class PayrollRun(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processed', 'Processed'),
        ('approved', 'Approved'),
        ('paid', 'Paid'),
    ]
    
    MONTH_CHOICES = [
        (1, 'January'),
        (2, 'February'),
        (3, 'March'),
        (4, 'April'),
        (5, 'May'),
        (6, 'June'),
        (7, 'July'),
        (8, 'August'),
        (9, 'September'),
        (10, 'October'),
        (11, 'November'),
        (12, 'December'),
    ]
    
    document_number = models.CharField(max_length=50, unique=True, null=True, blank=True)
    name = models.CharField(max_length=100, blank=True, help_text="Optional payroll run name")
    arabic_name = models.CharField(max_length=100, blank=True, help_text="arabic payroll run name")
    month = models.IntegerField(choices=MONTH_CHOICES, help_text="Month of the payroll period")
    year = models.IntegerField(help_text="Year of the payroll period")
    # 🔐 FROZEN ATTENDANCE DATES
    attendance_start_date = models.DateField(blank=True,null=True,)
    attendance_end_date = models.DateField(blank=True,null=True,)
    payment_date = models.DateField(null=True, blank=True, help_text="When employees will be paid")
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', on_delete=models.SET_NULL, null=True, blank=True)
    department = models.ForeignKey('OrganisationManager.dept_master', on_delete=models.SET_NULL, null=True, blank=True)
    employees = models.ManyToManyField('EmpManagement.emp_master',blank=True,null=True)
    category = models.ForeignKey('OrganisationManager.ctgry_master', on_delete=models.SET_NULL, null=True, blank=True)
    designation = models.ForeignKey('OrganisationManager.desgntn_master', on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        unique_together = ('month', 'year', 'branch', 'department', 'category')
        permissions = (
                ("add_wps", "Can add wps"),
                ("view_wps", "Can view wps"),
                ("change_wps", "Can change wps"),
                ("export_wps", "Can export wps"),
                ("delete_wps", "Can delete wps"),
        )
    def get_employees(self):
        from EmpManagement.models import emp_master
        try:
            # ✅ If specific employees are selected, return only them
            if self.employees.exists():
                return self.employees.all()

            # ✅ Otherwise fall back to branch/department/category filtering
            employees = emp_master.objects.all()
            if self.branch:
                employees = employees.filter(emp_branch_id=self.branch)
            if self.department:
                employees = employees.filter(emp_dept_id=self.department)
            if self.category:
                employees = employees.filter(emp_ctgry_id=self.category)

            return employees
        except Exception:
            return emp_master.objects.none()

    def get_month_display(self):
        return dict(self.MONTH_CHOICES).get(self.month, 'Unknown')

    def __str__(self):
        return f"Payroll - {self.get_month_display()} {self.year} ({self.status})"


class Payslip(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processed', 'Processed'),
        ('paid', 'Paid'),
        ('rejected', 'Rejected'),
        ('Approved', 'Approved'),
    ]

    payroll_run = models.ForeignKey(
        PayrollRun,
        on_delete=models.CASCADE,
        related_name='payslips'
    )

    employee = models.ForeignKey(
        'EmpManagement.emp_master',
        on_delete=models.CASCADE,
        related_name='payslips'
    )

    gross_salary = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    net_salary = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    total_deductions = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_additions = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    total_working_days = models.PositiveIntegerField(
        default=0,
        help_text="Total working days in the payroll period"
    )

    days_worked = models.PositiveIntegerField(
        default=0,
        help_text="Number of days the employee worked"
    )

    pro_rata_adjustment = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Pro-rata adjustment"
    )

    arrears = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Arrears amount"
    )

    send_email = models.BooleanField(
        default=False,
        help_text="Send this payslip via email if True"
    )

    payslip_pdf = models.FileField(
        upload_to='payslips/',
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=['pdf'])],
    )

    confirm_status = models.BooleanField(
        default=False,
        help_text="confirm this payslip if True"
    )

    trial_status = models.BooleanField(
        default=False,
        help_text="confirm this payslip if True"
    )

    rejection_reason = models.TextField(null=True, blank=True)

    currency = models.ForeignKey(
        "Core.crncy_mstr",
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    def save(self, *args, **kwargs):
        if not self.currency and self.employee and self.employee.emp_country_id:
            if hasattr(self.employee.emp_country_id, 'currency'):
                self.currency = self.employee.emp_country_id.currency.first()
        super().save(*args, **kwargs)

    def move_to_next_level(self):

        # -------- REJECT CHECK --------
        if self.approvals.filter(status__iexact='Rejected').exists():
            self.status = 'Rejected'
            self.save(update_fields=['status'])
            return

        # -------- CURRENT LEVEL --------
        current_level = self.approvals.filter(
            status__iexact='Approved'
        ).aggregate(
            max_level=Max('level')
        )['max_level'] or 0

        # -------- EMPLOYEE / BRANCH CHECK --------
        if not self.employee or not self.employee.emp_branch_id:
            return

        workflow = PayslipApprovalWorkflow.objects.filter(
            branch__id=self.employee.emp_branch_id.id
        ).first()

        if not workflow:
            return

        # -------- NEXT LEVEL --------
        next_level = workflow.payslip_levels.filter(
            level=current_level + 1
        ).first()

        if next_level:

            if workflow.approval_type == 'reporting_manager':
                approver = getattr(self.employee, 'emp_reporting_manager', None)
            else:
                approver = next_level.approver

            if not approver:
                return

            # ✅ prevent duplicate approvals
            if not PayslipApproval.objects.filter(
                request=self,
                level=next_level.level
            ).exists():

                PayslipApproval.objects.create(
                    request=self,
                    approver=approver,
                    role=next_level.role,
                    level=next_level.level,
                    status='Pending',
                    employee=self.employee
                )

        else:
            self.status = 'Approved'
            self.save(update_fields=['status'])


class PayslipApprovalWorkflow(models.Model):

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

    branch = models.ManyToManyField(
        'OrganisationManager.brnch_mstr',
        blank=True
    )


class PayslipCommonWorkflow(models.Model):

    workflow = models.ForeignKey(
        PayslipApprovalWorkflow,
        related_name='payslip_levels',
        on_delete=models.CASCADE,
        null=True
    )

    level = models.PositiveIntegerField()

    approver = models.ForeignKey(
        'UserManagement.CustomUser',
        on_delete=models.SET_NULL,
        null=True
    )

    role = models.CharField(max_length=100)

    class Meta:
        ordering = ['level']

    def __str__(self):
        return f"Level {self.level} - {self.role} ({self.approver})"


class PayslipApproval(models.Model):

    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
    ]

    request = models.ForeignKey(
        Payslip,
        on_delete=models.CASCADE,
        related_name='approvals'
    )

    employee = models.ForeignKey(
        'EmpManagement.emp_master',
        on_delete=models.CASCADE
    )

    approver = models.ForeignKey(
        'UserManagement.CustomUser',
        on_delete=models.SET_NULL,
        null=True
    )

    role = models.CharField(max_length=100, null=True, blank=True)

    level = models.PositiveIntegerField()

    note = models.TextField(null=True, blank=True)

    rejection_reason = models.TextField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='Pending'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.request} - {self.approver} - {self.status}"

    def approve(self, note=None):

        self.status = 'Approved'

        if note:
            self.note = note

        self.save(update_fields=['status', 'note'])

        self.request.move_to_next_level()

    def reject(self, rejection_reason, note=None):

        self.status = 'Rejected'

        if note:
            self.note = note

        self.rejection_reason = rejection_reason

        self.save(update_fields=[
            'status',
            'note',
            'rejection_reason'
        ])

        self.request.status = 'Rejected'

        # ✅ FIXED
        self.request.rejection_reason = rejection_reason

        self.request.save(update_fields=[
            'status',
            'rejection_reason'
        ])


@receiver(post_save, sender=Payslip)
def create_initial_payslip_approval(sender, instance, created, **kwargs):

    if not created:
        return

    # prevent duplicate initial approval
    if instance.approvals.exists():
        return

    # -------- SAFE BRANCH CHECK --------
    if not instance.employee or not instance.employee.emp_branch_id:
        return

    workflow = PayslipApprovalWorkflow.objects.filter(
        branch__id=instance.employee.emp_branch_id.id
    ).first()

    if not workflow:
        return

    approval_type = workflow.approval_type

    # -------- NO APPROVAL --------
    if approval_type == 'no_approval':

        PayslipApproval.objects.create(
            request=instance,
            approver=instance.employee.user if hasattr(instance.employee, 'user') else None,
            role="Auto Approval",
            level=1,
            status='Approved',
            employee=instance.employee
        )

        instance.status = 'Approved'
        instance.save(update_fields=['status'])

        return

    # -------- REPORTING MANAGER --------
    if approval_type == 'reporting_manager':

        manager = getattr(instance.employee, 'emp_reporting_manager', None)

        if manager:

            PayslipApproval.objects.create(
                request=instance,
                approver=manager,
                role="Reporting Manager",
                level=1,
                status='Pending',
                employee=instance.employee
            )

        else:
            instance.status = 'Approved'
            instance.save(update_fields=['status'])

        return

    # -------- MULTI APPROVAL --------
    if approval_type == 'multi_approval':

        first_level = PayslipCommonWorkflow.objects.filter(
            workflow=workflow
        ).order_by('level').first()

        if not first_level:
            return

        if not first_level.approver:
            return

        PayslipApproval.objects.create(
            request=instance,
            approver=first_level.approver,
            role=first_level.role,
            level=first_level.level,
            status='Pending',
            employee=instance.employee
        )

        return
    
class PayslipComponent(models.Model):
    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name='components')
    component = models.ForeignKey(SalaryComponent, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    def __str__(self):
        return f"{self.payslip.employee} - {self.component.name} ({self.amount})"
class PayslipLeave(models.Model):
    payslip = models.ForeignKey(
        "PayrollManagement.Payslip",
        on_delete=models.CASCADE,
        related_name="leave_details"
    )
    leave_type = models.ForeignKey(
        "calendars.leave_type",
        on_delete=models.CASCADE
    )
    days = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        unique_together = ("payslip", "leave_type")

    def __str__(self):
        return f"{self.leave_type} - {self.days} days"
class LoanCommonWorkflow(models.Model):
    level    = models.IntegerField()
    role     = models.CharField(max_length=50, null=True, blank=True)
    approver = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True, on_delete=models.SET_NULL)
    created_at         = models.DateTimeField(auto_now_add=True)
    created_by         = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['level'], name='Loan_common_workflow_levels')
        ]
    def __str__(self):
        return f"Level {self.level} - {self.role or self.approver}"
    
class LoanEmailTemplate(models.Model):
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
class LoanNotification(models.Model):
    recipient_user = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True, on_delete=models.CASCADE)
    recipient_employee = models.ForeignKey('EmpManagement.emp_master', null=True, blank=True, on_delete=models.CASCADE, related_name='loan_notification')
    message = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    deligate_user = models.ForeignKey('UserManagement.CustomUser',null=True,blank=True,on_delete=models.CASCADE,related_name='loan_deligated_notifications')

    def __str__(self):
        if self.recipient_user:
            return f"Notification for {self.recipient_user.emp_code}: {self.message}"
        else:
            return f"Notification for employee: {self.message}"

class LoanType(models.Model):
    loan_type           = models.CharField(max_length=255)  # e.g., Personal, Housing, Car
    max_amount          = models.DecimalField(max_digits=10, decimal_places=2)
    repayment_period    = models.PositiveIntegerField()  # in months
    min_approvals_required        = models.PositiveIntegerField(null=True, blank=True, help_text="Minimum number of approvals required to approve the request")
    created_at          = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)
    use_common_workflow = models.BooleanField(default=False)
    created_by          = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    branch = models.ManyToManyField('OrganisationManager.brnch_mstr',blank=True)


    def __str__(self):
        return f"{self.loan_type}"

class  LoanApplication(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
        ('Disbursed', 'Disbursed'),
        ('Rejected', 'Rejected'),
        ('Paused', 'Paused'),
        ('In Progress', 'In Progress'),
        ('Closed', 'Closed'),

    ]

    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE,related_name="loan")
    document_number = models.CharField(max_length=50, unique=True, null=True, blank=True)
    branch           =  models.ForeignKey('OrganisationManager.brnch_mstr',on_delete=models.SET_NULL, null=True)
    loan_type = models.ForeignKey(LoanType, on_delete=models.CASCADE)
    amount_requested = models.DecimalField(max_digits=10, decimal_places=2)
    repayment_period = models.PositiveIntegerField()  # In months
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    emi_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    disbursement_date = models.DateField(null=True, blank=True)
    remaining_balance = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    applied_on = models.DateTimeField(auto_now_add=True)
    approved_on = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(null=True, blank=True)
    pause_start_date = models.DateField(null=True, blank=True)
    resume_date = models.DateField(null=True, blank=True)
    pause_reason = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.CASCADE, null=True, blank=True)

    def clean(self):
        """
        Custom validation to ensure no duplicate active loans of the same type.
        """
        # Check for existing loans of the same type for the same employee
        existing_loans = LoanApplication.objects.filter(
            employee=self.employee,
            loan_type=self.loan_type,
            status__in=['Pending', 'Approved', 'Disbursed', 'Paused', 'In Progress']
        ).exclude(pk=self.pk)  # Exclude the current instance during update

        if existing_loans.exists():
            raise ValidationError(
                f"An active loan of type '{self.loan_type}' already exists for this employee."
            )

    def save(self, *args, **kwargs):
        self.clean()
        """Override save to initialize remaining balance and EMI."""
        if self.amount_requested and self.repayment_period and not self.emi_amount:
            self.emi_amount = round(self.amount_requested / self.repayment_period, 2)
        if self.remaining_balance is None:
            self.remaining_balance = self.amount_requested
        super().save(*args, **kwargs)

    
    def pause(self, start_date, reason=None):
        """Pause the loan repayments."""
        if self.status not in ['Approved']:
            raise ValidationError("Only active loans can be paused.")
        self.status = 'Paused'
        self.pause_start_date = start_date
        self.pause_reason = reason
        self.save()

    def resume(self, resume_date, reason=None):
        """Resume the loan repayments."""
        if self.status != 'Paused':
            raise ValidationError("Loan is not currently paused.")
        self.status = 'Approved'
        self.resume_date = resume_date
        self.resume_reason = reason
        self.save()
        def __str__(self):
            return f"{self.employee} - {self.loan_type} - {self.status}"
      
    
    def move_to_next_level(self):
        from .utils import loan_schedule_escalation

        # ---------------- REJECT CHECK ----------------
        if self.approvals.filter(status=LoanApproval.REJECTED).exists():
            self.status = 'Rejected'
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Request Rejected",
                notification_type="loan",
                message=(f"A LoanRequest {self.loan_type}"
                        f"(Document No: {self.document_number}) has been Rejected."
                        ),
                template_type="request_rejected",
                context={
                    **get_employee_context(self.employee),
                    'document_number': self.document_number,
                    'loan_type': self.loan_type.loan_type,
                    'amount_requested': self.amount_requested,
                    'repayment_period': self.repayment_period,
                    'emi_amount': self.emi_amount,
                    'remaining_balance': self.remaining_balance,
                    'status': self.status,
                    'rejection_reason': self.rejection_reason,
                },
                email_template_model=LoanEmailTemplate,
                notification_model=LoanNotification
            )
            return
        

          # ---------------- GET WORKFLOW ----------------
        workflow = LoanApprovalWorkflow.objects.filter(
            loan_type=self.loan_type,
            branch=self.employee.emp_branch_id
        ).first()

        if not workflow:
            workflow = LoanApprovalWorkflow.objects.filter(
                loan_type=self.loan_type
            ).first()

        if not workflow:
            workflow = LoanApprovalWorkflow.objects.create(
                loan_type=self.loan_type,
                approval_type="no_approval"
            )

            LoanApprovalLevels.objects.create(
                workflow=workflow,
                level=1,
                role="Auto Approval",
                approver=None
            )
        approval_type = workflow.approval_type

        # ---------------- MINIMUM APPROVAL CHECK ----------------
        approved_count = self.approvals.filter(status=LoanApproval.APPROVED).count()
        min_required = self.loan_type.min_approvals_required

        if min_required and approved_count >= min_required:
            self.status = 'Approved'
            self.approved_on = timezone.now()
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Request Approved",
                notification_type="loan",
                message=(f"A LoanRequest {self.loan_type}"
                        f"(Document No: {self.document_number}) has been Approved."
                        ),
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    'loan_type': self.loan_type.loan_type,
                    'document_number': self.document_number,
                    'amount_requested': self.amount_requested,
                    'repayment_period': self.repayment_period,
                    'emi_amount': self.emi_amount,
                    'remaining_balance': self.remaining_balance,
                    'status': self.status,
                },
                email_template_model=LoanEmailTemplate,
                notification_model=LoanNotification
            )
            return
        
        # =========================================================
    # NO APPROVAL
    # =========================================================
        if approval_type == "no_approval":

            self.status = "Approved"
            self.approved_on = timezone.now()
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Request Approved",
                notification_type="loan",
                message=(f"A LoanRequest {self.loan_type}"
                        f"(Document No: {self.document_number}) has been AutoApproved."
                        ),
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    "loan_type": self.loan_type.loan_type,
                    "document_number": self.document_number,
                    "amount_requested": self.amount_requested,
                    "repayment_period": self.repayment_period,
                    "emi_amount": self.emi_amount,
                    "remaining_balance": self.remaining_balance,
                    "status": self.status,
                },
                email_template_model=LoanEmailTemplate,
                notification_model=LoanNotification,
            )
            return

        # =========================================================
        # REPORTING MANAGER
        # =========================================================
        if approval_type == "reporting_manager":

            if self.approvals.filter(status=LoanApproval.APPROVED).exists():

                self.status = "Approved"
                self.approved_on = timezone.now()
                self.save()

                send_notification_email(
                    user=self.created_by,
                    employee=self.employee,
                    branch=self.branch,
                    title="Request Approved",
                    notification_type="loan",
                    message=(f"A LoanRequest {self.loan_type}"
                        f"(Document No: {self.document_number}) has been Approved by ReportingManager."
                        ),
                    template_type="request_approved",
                    context={
                        **get_employee_context(self.employee),
                        "loan_type": self.loan_type.loan_type,
                        "document_number": self.document_number,
                        "amount_requested": self.amount_requested,
                        "repayment_period": self.repayment_period,
                        "emi_amount": self.emi_amount,
                        "remaining_balance": self.remaining_balance,
                        "status": self.status,
                    },
                    email_template_model=LoanEmailTemplate,
                    notification_model=LoanNotification,
                )

            return

        # =========================================================
        # MULTI APPROVAL
        # =========================================================

        last_approved = self.approvals.filter(
            status=LoanApproval.APPROVED
        ).order_by("-level").first()

        current_level = (last_approved.level + 1) if last_approved else 1

        if self.approvals.filter(
            level=current_level,
            status=LoanApproval.PENDING
        ).exists():
            return

        # Get next approval level
        if self.loan_type.use_common_workflow:
            next_level = LoanCommonWorkflow.objects.filter(
                level=current_level
            ).first()
        else:
            next_level = workflow.loan_levels.filter(
                level=current_level
            ).first()

        if next_level and next_level.approver:

            last_approval = self.approvals.order_by("-level", "-id").first()

            note_to_carry = None
            if last_approval and last_approval.note:
                if not last_approval.note.startswith(("Escalated to", "Escalated from")):
                    note_to_carry = last_approval.note

            approval = LoanApproval.objects.create(
                loan_request=self,
                approver=next_level.approver,
                role=next_level.role,
                level=next_level.level,
                status=LoanApproval.PENDING,
                note=note_to_carry,
                employee_id=self.employee.id,
            )

            # Schedule escalation
            loan_schedule_escalation(approval, next_level)

            # Notify next approver
            send_notification_email(
                user=next_level.approver,
                employee=None,
                branch=self.branch,
                title="Loan Approval Required",
                notification_type="loan",
                message=(f"A LoanRequest {self.loan_type}"
                        f"(Document No: {self.document_number}) is waiting for your Approval."
                        ),
                template_type="request_created",
                context={
                    **get_employee_context(self.employee),
                    "loan_type": self.loan_type.loan_type,
                    "document_number": self.document_number,
                    "amount_requested": self.amount_requested,
                    "repayment_period": self.repayment_period,
                    "emi_amount": self.emi_amount,
                    "remaining_balance": self.remaining_balance,
                    "status": self.status,
                },
                email_template_model=LoanEmailTemplate,
                notification_model=LoanNotification,
            )

        else:
            # Final Approval
            self.status = "Approved"
            self.approved_on = timezone.now()
            self.save()

            # Notify employee/request creator
            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Loan Request Approved",
                notification_type="loan",
                message=(f"A LoanRequest {self.loan_type}"
                        f"(Document No: {self.document_number}) has been FullyApproved."
                        ),
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    "loan_type": self.loan_type.loan_type,
                    "document_number": self.document_number,
                    "amount_requested": self.amount_requested,
                    "repayment_period": self.repayment_period,
                    "emi_amount": self.emi_amount,
                    "remaining_balance": self.remaining_balance,
                    "status": self.status,
                },
                email_template_model=LoanEmailTemplate,
                notification_model=LoanNotification,
            )                 
            

class LoanRepayment(models.Model):
    loan = models.ForeignKey(LoanApplication, on_delete=models.CASCADE)
    payslip = models.ForeignKey(Payslip, on_delete=models.SET_NULL, null=True, blank=True)  # ADD THIS
    repayment_date = models.DateField(auto_now_add=True,null=True)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2)
    remaining_balance = models.DecimalField(max_digits=10, decimal_places=2)
    
    def save(self, *args, **kwargs):
        """Ensure repayments don't result in negative balance."""
        if self.remaining_balance < 0:
            self.remaining_balance = 0
        super().save(*args, **kwargs)
    def __str__(self):
        return f"{self.loan} - {self.repayment_date}"

class LoanApprovalWorkflow(models.Model):
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
    loan_type = models.ForeignKey('LoanType', related_name='loan_approval_workflows', on_delete=models.CASCADE, null=True, blank=True)  # Nullable for common workflow
    branch = models.ManyToManyField('OrganisationManager.brnch_mstr', blank=True)




class LoanApprovalLevels(models.Model):
    workflow = models.ForeignKey(LoanApprovalWorkflow,related_name='loan_levels',on_delete=models.CASCADE,null=True)
    level            = models.IntegerField()
    role             = models.CharField(max_length=50, null=True, blank=True)  # Use this for role-based approval like 'CEO' or 'Manager'
    approver         = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True, on_delete=models.SET_NULL)  # Use this for user-based approval
    escalate_to = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True, blank=True,related_name='loan_escalated_levels')
    escalate_after_days = models.PositiveIntegerField(default=0, help_text="Escalate after X days if pending")
    escalate_after_hours = models.PositiveIntegerField(default=0, help_text="Escalate after X hours if pending")
    escalate_after_minutes = models.PositiveIntegerField(default=0, help_text="Escalate after X minutes if pending")
    def get_escalation_timedelta(self):
        """Returns the total time delta for escalation."""
        from datetime import timedelta
        total_minutes = (self.escalate_after_days * 24 * 60) + (self.escalate_after_hours * 60) + self.escalate_after_minutes
        return timedelta(minutes=total_minutes)

    class Meta:
        unique_together = ('workflow','level')
        permissions = (
                    ("add_loan_escalation", "Can add Escalation"),
                    ("view_loan_escalation", "Can view Escalation"),
                    ("change_loan_escalation", "Can change Escalation"),
                    ("export_loan_escalation", "Can export Escalation"),
                    ("delete_loan_escalation", "Can delete Escalation"),
            )
class LoanApproval(models.Model):
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
    loan_request         = models.ForeignKey(LoanApplication, related_name='approvals', on_delete=models.CASCADE,null=True, blank=True)
    approver             = models.ForeignKey('UserManagement.CustomUser', on_delete=models.CASCADE)
    role                 = models.CharField(max_length=50, null=True, blank=True)
    level                = models.IntegerField(default=1)
    status               = models.CharField(max_length=20, choices=STATUS_CHOICES,default=PENDING)
    note                 = models.TextField(null=True, blank=True)
    deligate_to         = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,blank=True,related_name='loan_deligations_received')
    is_deligate         = models.BooleanField(default=False)
    deligate_response = models.TextField(null=True, blank=True)
    escalated = models.BooleanField(default=False)
    escalated_at = models.DateTimeField(null=True, blank=True)
    is_escalation = models.BooleanField(default=False)
    rejection_reason     = models.TextField(null=True, blank=True)
    created_at           = models.DateField(auto_now_add=True)
    updated_at           = models.DateField(auto_now=True)
    employee_id          = models.IntegerField(null=True, blank=True)

    def approve(self, note=None):
        self.status = self.APPROVED
        if note:
            self.note = note
        self.save()
        if self.loan_request:
            self.loan_request.move_to_next_level()
        

    def reject(self, rejection_reason, note=None):
        if rejection_reason:
            self.rejection_reason = rejection_reason
        self.status = self.REJECTED
        if note:
            self.note = note
        self.save()
        if self.loan_request:
            self.loan_request.status = 'Rejected'
            self.loan_request.save()
        send_notification_email(
        user=self.loan_request.created_by,
        employee=self.loan_request.employee,
        branch=self.branch,
        title="Request Rejected",
        notification_type="loan",
        message=(f"A LoanRequest {self.loan_type}"
                f"(Document No: {self.document_number}) has been Rejected."),
        template_type="request_rejected",
        context={
            **get_employee_context(self.loan_request.employee),
            'loan_type': self.loan_request.loan_type.loan_type,
            'document_number': self.loan_request.document_number,
            'amount_requested': self.loan_request.amount_requested,
            'repayment_period': self.loan_request.repayment_period,
            'emi_amount': self.loan_request.emi_amount,
            'remaining_balance': self.loan_request.remaining_balance,
            'status': self.loan_request.status,
            'rejection_reason': self.rejection_reason,
            
        },
        email_template_model=LoanEmailTemplate,
        notification_model=LoanNotification
    )
    
        
@receiver(post_save, sender=LoanApplication)
def create_initial_loan_approval(sender, instance, created, **kwargs):

    if not created:
        return
    
    with transaction.atomic():

        # ---------------- GET WORKFLOW ----------------
        if instance.loan_type.use_common_workflow:
            first_level = LoanCommonWorkflow.objects.order_by("level").first()
            workflow = None
        else:
            workflow = LoanApprovalWorkflow.objects.filter(
                loan_type=instance.loan_type,
                branch=instance.employee.emp_branch_id
            ).first()

            # Same as GeneralRequest
            if not workflow:
                workflow = LoanApprovalWorkflow.objects.create(
                    loan_type=instance.loan_type,
                    approval_type="no_approval"
                )

            first_level = workflow.loan_levels.order_by("level").first()

        # Same as GeneralRequest
        if workflow and not first_level:
            first_level = LoanApprovalLevels.objects.create(
                workflow=workflow,
                level=1,
                role="Auto Level",
                approver=None
            )

        approval_type = workflow.approval_type if workflow else "no_approval"

        # =========================================================
        # NO APPROVAL
        # =========================================================

        if approval_type == "no_approval":

            approver = getattr(instance.employee, "users", None) or instance.created_by

            if not approver:
                raise Exception("Employee does not have a system user assigned.")

            LoanApproval.objects.create(
                loan_request=instance,
                approver=approver,
                role="Auto Approval",
                level=1,
                status=LoanApproval.APPROVED,
                employee_id=instance.employee.id
            )

            instance.status = "Approved"
            instance.save(update_fields=["status"])

            send_notification_email(
                user=approver,
                employee=instance.employee,
                branch=instance.employee.emp_branch_id,
                title="Request Approved",
                notification_type="loan",
                message=(f"A LoanRequest {instance.loan_type}"
                        f"(Document No: {instance.document_number}) has been AutoApproved."
                        ),
                template_type="request_approved",
                context={
                    **get_employee_context(instance.employee),
                    "loan_type": instance.loan_type.loan_type,
                    "document_number": instance.document_number,
                    "amount_requested": instance.amount_requested,
                    "repayment_period": instance.repayment_period,
                    "emi_amount": instance.emi_amount,
                    "remaining_balance": instance.remaining_balance,
                    "status": instance.status,
                },
                email_template_model=LoanEmailTemplate,
                notification_model=LoanNotification,
            )

            return

        # =========================================================
        # REPORTING MANAGER
        # =========================================================

        if approval_type == "reporting_manager":

            manager = getattr(instance.employee, "emp_reporting_manager", None)

            if not manager:
                raise Exception("Employee has no reporting manager.")

            LoanApproval.objects.create(
                loan_request=instance,
                approver=manager,
                role="Reporting Manager",
                level=1,
                status=LoanApproval.PENDING,
                employee_id=instance.employee.id
            )

            send_notification_email(
                user=manager,
                employee=instance.employee,
                branch=instance.employee.emp_branch_id,
                title="Request Created",
                notification_type="loan",
                message=(f"A LoanRequest {instance.loan_type}"
                        f"(Document No: {instance.document_number}) is waiting for your Approval."
                        ),
                template_type="request_created",
                context={
                    **get_employee_context(instance.employee),
                    "loan_type": instance.loan_type.loan_type,
                    "document_number": instance.document_number,
                    "amount_requested": instance.amount_requested,
                    "repayment_period": instance.repayment_period,
                    "emi_amount": instance.emi_amount,
                    "remaining_balance": instance.remaining_balance,
                    "status": instance.status,
                },
                email_template_model=LoanEmailTemplate,
                notification_model=LoanNotification,
            )

            return

        # =========================================================
        # MULTI APPROVAL
        # =========================================================

        if approval_type == "multi_approval":

            if first_level and first_level.approver:

                LoanApproval.objects.create(
                    loan_request=instance,
                    approver=first_level.approver,
                    role=first_level.role,
                    level=first_level.level,
                    status=LoanApproval.PENDING,
                    employee_id=instance.employee.id
                )

                send_notification_email(
                    user=first_level.approver,
                    employee=instance.employee,
                    branch=instance.employee.emp_branch_id,
                    title="Request Created",
                    notification_type="loan",
                    message=(f"A LoanRequest {instance.loan_type}"
                            f"(Document No: {instance.document_number}) is waiting for your Approval."
                        ),
                    template_type="request_created",
                    context={
                        **get_employee_context(instance.employee),
                        "loan_type": instance.loan_type.loan_type,
                        "document_number": instance.document_number,
                        "amount_requested": instance.amount_requested,
                        "repayment_period": instance.repayment_period,
                        "emi_amount": instance.emi_amount,
                        "remaining_balance": instance.remaining_balance,
                        "status": instance.status,
                    },
                    email_template_model=LoanEmailTemplate,
                    notification_model=LoanNotification,
                )

            return
    
class AdvanceSalaryEmailTemplate(models.Model):
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
class AdvanceSalaryNotification(models.Model):
    recipient_user = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True, on_delete=models.CASCADE)
    recipient_employee = models.ForeignKey('EmpManagement.emp_master', null=True, blank=True, on_delete=models.CASCADE, related_name='advance_salary_notification')
    message = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    deligate_user = models.ForeignKey('UserManagement.CustomUser',null=True,blank=True,on_delete=models.CASCADE,related_name='advsalary_deligated_notifications')
    def __str__(self):
        if self.recipient_user:
            return f"Notification for {self.recipient_user.emp_code}: {self.message}"
        else:
            return f"Notification for employee: {self.message}"
           
class AdvanceSalaryRequest(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
        ('Cancelled', 'Cancelled'),
        ('Deducted', 'Deducted'),
        ('Paused', 'Paused'),
        
    ]

    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE, related_name='advance_salary_requests')
    document_number = models.CharField(max_length=50, unique=True, null=True, blank=True)
    branch           =  models.ForeignKey('OrganisationManager.brnch_mstr',on_delete=models.SET_NULL, null=True)
    requested_amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    remarks = models.TextField(blank=True, null=True)
    pause_start_date = models.DateField(null=True, blank=True)
    resume_date = models.DateField(null=True, blank=True)
    pause_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.CASCADE, null=True, blank=True)


    def __str__(self):
        return f"{self.employee} - {self.requested_amount} - {self.status}"
    
    def move_to_next_level(self):
        from .utils import schedule_escalation

        # ❌ If rejected → stop
        if self.approvals.filter(status='Rejected').exists():
            self.status = 'Rejected'
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                branch=self.branch,
                title="Request Rejected",
                notification_type="request",
                message=f"Your AdvanceSalaryRequest {self.document_number} has been Rejected.",
                template_type="request_rejected",
                context={
                    **get_employee_context(self.employee),
                    'document_number': self.document_number,
                    'requested_amount': self.requested_amount,
                    'reason': self.reason,
                    'remarks': self.remarks,
                },
                email_template_model=AdvanceSalaryEmailTemplate,
                notification_model=AdvanceSalaryNotification
            )
            return

        # ---------------- MINIMUM APPROVAL CHECK ----------------
        approved_count = self.approvals.filter(status='Approved').count()
        min_required = getattr(self, 'min_approvals_required', None)

        if min_required and approved_count >= min_required:
            self.status = 'Approved'
            self.approval_date = timezone.now()
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                message=f"Your AdvanceSalaryRequest {self.document_number} has been Approved.",
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    'document_number': self.document_number,
                    'requested_amount': self.requested_amount,
                    'reason': self.reason,
                    'remarks': self.remarks,
                },
                email_template_model=AdvanceSalaryEmailTemplate,
                notification_model=AdvanceSalaryNotification
            )
            return

        # ---------------- CURRENT LEVEL ----------------
        last_approved = self.approvals.filter(
            status='Approved'
        ).order_by('-level').first()

        current_level = last_approved.level if last_approved else 0

        # ---------------- WORKFLOW ----------------
        workflow = AdvanceApprovalWorkflow.objects.filter(
            branch=self.employee.emp_branch_id
        ).first()

        if not workflow:
            return

        # ---------------- NEXT LEVEL ----------------
        next_level = workflow.advance_levels.filter(
            level=current_level + 1
        ).first()

        if next_level:

            # same logic
            if workflow.approval_type == 'reporting_manager':
                approver = self.employee.emp_reporting_manager
            else:
                approver = next_level.approver

            if not approver:
                raise Exception(f"No approver configured for level {next_level.level}")

            last_approval = self.approvals.order_by('-level', '-id').first()

            note_to_carry = None
            if last_approval and last_approval.note:
                if not (
                    last_approval.note.startswith("Escalated to") or
                    last_approval.note.startswith("Escalated from")
                ):
                    note_to_carry = last_approval.note

            # ---------------- PREVENT DUPLICATE LEVEL ----------------
            if not AdvanceSalaryApproval.objects.filter(
                request=self,
                level=next_level.level,
                status__in=['Pending', 'Approved', 'Escalated']
            ).exists():

                new_approval = AdvanceSalaryApproval.objects.create(
                    request=self,
                    approver=approver,
                    role=next_level.role,
                    level=next_level.level,
                    status='Pending',
                    employee=self.employee,
                    note=note_to_carry
                )

                # ---------------- ESCALATION (FIXED) ----------------
                if (
                    hasattr(next_level, "get_escalation_timedelta")
                    and next_level.get_escalation_timedelta()
                    and next_level.get_escalation_timedelta().total_seconds() > 0
                    and not getattr(new_approval, "escalated", False)
                ):
                    schedule_escalation(
                        approval=new_approval,
                        level=next_level
                    )

                # ---------------- NOTIFICATION ----------------
                send_notification_email(
                    user=approver,
                    employee=None,
                    message=f"Your AdvanceSalaryRequest {self.document_number} is waiting for your Approval.",
                    template_type="request_created",
                    context={
                        **get_employee_context(self.employee),
                        'document_number': self.document_number,
                        'requested_amount': self.requested_amount,
                        'reason': self.reason,
                        'remarks': self.remarks,
                    },
                    email_template_model=AdvanceSalaryEmailTemplate,
                    notification_model=AdvanceSalaryNotification
                )

        else:
            # ---------------- FINAL APPROVAL ----------------
            self.status = 'Approved'
            self.approval_date = timezone.now()
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                message=f"Your AdvanceSalaryRequest {self.document_number} has been Approved.",
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    'document_number': self.document_number,
                    'requested_amount': self.requested_amount,
                    'reason': self.reason,
                    'remarks': self.remarks,
                },
                email_template_model=AdvanceSalaryEmailTemplate,
                notification_model=AdvanceSalaryNotification
            )
            return
    def pause(self, start_date, reason=None):
        """Pause the loan repayments."""
        if self.status not in ['Approved']:
            raise ValidationError("Only active advance salary request can be paused.")
        self.status = 'Paused'
        self.pause_start_date = start_date
        self.pause_reason = reason
        self.save()

    def resume(self, resume_date, reason=None):
        """Resume the loan repayments."""
        if self.status != 'Paused':
            raise ValidationError("Loan is not currently paused.")
        self.status = 'Approved'
        self.resume_date = resume_date
        self.resume_reason = reason
        self.save()
        def __str__(self):
            return f"{self.employee} - {self.loan_type} - {self.status}"

class AdvanceApprovalWorkflow(models.Model):
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
    branch = models.ManyToManyField('OrganisationManager.brnch_mstr', blank=True)
    


class AdvanceCommonWorkflow(models.Model):
    workflow = models.ForeignKey(AdvanceApprovalWorkflow, related_name='advance_levels', on_delete=models.CASCADE, null=True)
    level = models.PositiveIntegerField()
    approver = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True)
    role = models.CharField(max_length=100,blank=True,null=True)
    escalate_to = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True, blank=True,related_name='adv_salary_escalated_levels')
    escalate_after_days = models.PositiveIntegerField(default=0, help_text="Escalate after X days if pending")
    escalate_after_hours = models.PositiveIntegerField(default=0, help_text="Escalate after X hours if pending")
    escalate_after_minutes = models.PositiveIntegerField(default=0, help_text="Escalate after X minutes if pending")
    class Meta:
        ordering = ['level']
        permissions = (
                ("add_advsalary_escalation", "Can add Escalation"),
                ("view_advsalary_escalation", "Can view Escalation"),
                ("change_advsalary_escalation", "Can change Escalation"),
                ("export_advsalary_escalation", "Can export Escalation"),
                ("delete_advsalary_escalation", "Can delete Escalation"),
        )
    def get_escalation_timedelta(self):
        """Returns the total time delta for escalation."""
        from datetime import timedelta
        total_minutes = (self.escalate_after_days * 24 * 60) + (self.escalate_after_hours * 60) + self.escalate_after_minutes
        return timedelta(minutes=total_minutes)

        

    def __str__(self):
        return f"Level {self.level} - {self.role} ({self.approver})"
class AdvanceSalaryApproval(models.Model):
    
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

    request = models.ForeignKey(AdvanceSalaryRequest, on_delete=models.CASCADE, related_name='approvals')
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE)
    approver = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True)
    role = models.CharField(max_length=100, null=True, blank=True)
    level = models.PositiveIntegerField()
    note = models.TextField(null=True, blank=True)
    deligate_to     = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,blank=True,related_name='advsalary_deligations_received')
    is_deligate     = models.BooleanField(default=False)
    deligate_response = models.TextField(null=True, blank=True)
    escalated = models.BooleanField(default=False)
    escalated_at = models.DateTimeField(null=True, blank=True)
    is_escalation = models.BooleanField(default=False)
    rejection_reason     = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by      = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    updated_at      = models.DateField(auto_now=True)

    def __str__(self):
        return f"{self.request} - {self.approver} - {self.status}"

    def approve(self, note=None):
        self.status = 'Approved'
        if note:
            self.note = note
        self.save()
        self.request.move_to_next_level()

    def reject(self, rejection_reason, note=None):
        self.status = 'Rejected'
        if note:
            self.note = note
        self.save()
        self.request.status = 'Rejected'
        self.request.remarks = rejection_reason
        self.request.save()
        send_notification_email(
        user=self.request.created_by,
        employee=self.request.employee,
        message=f"Your AdvanceSalaryRequest {self.document_number} has been Rejected.",
        template_type="request_rejected",
        context={
            **get_employee_context(self.request.employee),
            'document_number': self.request.document_number,
            'requested_amount': self.request.requested_amount,
            'reason': self.request.reason,
            'remarks': self.request.remarks,
            'rejection_reason':self.rejection_reason,
            
        },
        email_template_model=AdvanceSalaryEmailTemplate,
        notification_model=AdvanceSalaryNotification
    )
@receiver(post_save, sender=AdvanceSalaryRequest)
def create_initial_advance_approval(sender, instance, created, **kwargs):
    if not created:
        return

    # ✅ FIX 1: correct branch filtering + stable selection
    workflow = AdvanceApprovalWorkflow.objects.filter(
        branch=instance.employee.emp_branch_id
    ).order_by('-id').first()

    if not workflow:
        return

    approval_type = workflow.approval_type

    # ---------------- NO APPROVAL ----------------
    if approval_type == 'no_approval':
        approver = instance.created_by or getattr(instance.employee, 'users', None)

        if not approver:
            raise Exception("Employee has no system user.")

        AdvanceSalaryApproval.objects.create(
            request=instance,
            approver=approver,
            role="Auto Approval",
            level=1,
            status=AdvanceSalaryApproval.APPROVED,
            employee=instance.employee
        )

        instance.status = "Approved"
        instance.save(update_fields=['status'])

        send_notification_email(
            user=approver,
            employee=instance.employee,
            message=f"Your AdvanceSalaryRequest {instance.document_number} has been AutoApproved.",
            template_type="request_approved",
            context={
                **get_employee_context(instance.employee),
                'document_number': instance.document_number,
                'requested_amount': instance.requested_amount,
                'reason': instance.reason,
                'status': instance.status,
            },
            email_template_model=AdvanceSalaryEmailTemplate,
            notification_model=AdvanceSalaryNotification
        )
        return

    # ---------------- REPORTING MANAGER ----------------
    if approval_type == 'reporting_manager':
        manager = instance.employee.emp_reporting_manager

        if not manager:
            raise Exception("Employee has no reporting manager.")

        AdvanceSalaryApproval.objects.create(
            request=instance,
            approver=manager,
            role="Reporting Manager",
            level=1,
            status=AdvanceSalaryApproval.PENDING,
            employee=instance.employee
        )

        send_notification_email(
            user=manager,
            employee=None,
            message=f"Your AdvanceSalaryRequest {instance.document_number} is waiting for your Approval.",
            template_type="request_created",
            context={
                **get_employee_context(instance.employee),
                'document_number': instance.document_number,
                'requested_amount': instance.requested_amount,
                'reason': instance.reason,
            },
            email_template_model=AdvanceSalaryEmailTemplate,
            notification_model=AdvanceSalaryNotification
        )
        return

    # ---------------- MULTI APPROVAL ----------------
    if approval_type == 'multi_approval':

        first_level = workflow.advance_levels.order_by('level').first()

        if not first_level:
            return

        # ✅ FIX 2: approver safety
        if not first_level.approver:
            raise Exception(f"No approver configured for level {first_level.level}")

        AdvanceSalaryApproval.objects.create(
            request=instance,
            approver=first_level.approver,
            role=first_level.role,
            level=first_level.level,
            status=AdvanceSalaryApproval.PENDING,
            employee=instance.employee
        )

        send_notification_email(
            user=first_level.approver,
            employee=None,
            message=f"Your AdvanceSalaryRequest {instance.document_number} is waiting for your Approval.",
            template_type="request_created",
            context={
                **get_employee_context(instance.employee),
                'document_number': instance.document_number,
                'requested_amount': instance.requested_amount,
                'reason': instance.reason,
            },
            email_template_model=AdvanceSalaryEmailTemplate,
            notification_model=AdvanceSalaryNotification
        )
        return
    
class AirTicketPolicy(models.Model):
    ALLOWANCE_TYPE_CHOICES = [
        ('CASH', 'Cash'),
        ('TICKET', 'Ticket'),
        ('BOTH', 'Both'),
    ]
    TRAVEL_CLASS_CHOICES = [
        ('ECONOMY', 'Economy'),
        ('BUSINESS', 'Business'),
        ('FIRST', 'First Class'),
    ]
    name = models.CharField(max_length=100)
    allowed_in_probation = models.BooleanField(default=False)
    frequency_years = models.PositiveIntegerField(default=1)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    allowance_type = models.CharField(max_length=10, choices=ALLOWANCE_TYPE_CHOICES, default='TICKET')
    country =models.ForeignKey('Core.cntry_mstr',on_delete=models.CASCADE, related_name='air_ticket_country')
    eligible_departments = models.ManyToManyField('OrganisationManager.dept_master', blank=True)
    eligible_designations = models.ManyToManyField('OrganisationManager.desgntn_master', blank=True)
    eligible_categories = models.ManyToManyField('OrganisationManager.ctgry_master', blank=True)
    branch = models.ManyToManyField('OrganisationManager.brnch_mstr',blank=True)
    travel_class = models.CharField(max_length=20, choices=TRAVEL_CLASS_CHOICES, default='ECONOMY')
    is_active = models.BooleanField(default=True)
    def clean(self):
        if self.amount <= 0:
            raise ValidationError("Amount must be positive.")
        if AirTicketPolicy.objects.filter(
            country=self.country,
            eligible_departments__in=self.eligible_departments.all(),
            eligible_categories__in=self.eligible_categories.all(),
            is_active=True
        ).exclude(pk=self.pk).exists():
            raise ValidationError("An active policy already exists for this country, department, and category combination.")

    def __str__(self):
        return f"{self.name} - {self.allowance_type}"
class AirTicketRule(models.Model):
    RULE_TYPE_CHOICES = [
        ('ONE_WAY', 'One Way Ticket'),
        ('TWO_WAY', 'Two Way Ticket'),
        ('ENCASHMENT', 'Encashment'),
    ]

    policy = models.ForeignKey(AirTicketPolicy, on_delete=models.CASCADE, related_name='rules')
    rule_type = models.CharField(max_length=20, choices=RULE_TYPE_CHOICES)
    required_service_years = models.PositiveIntegerField(help_text='Minimum years of service for this rule')

    apply_in_next_payroll = models.BooleanField(default=False, help_text='For encashment: Apply in next payroll?')
    remarks = models.TextField(blank=True)

    def __str__(self):
        return f"{self.rule_type} after {self.required_service_years} year(s)"

class AirTicketAllocation(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE, related_name='air_ticket_allocations')
    policy = models.ForeignKey(AirTicketPolicy, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    remaining_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    allocated_date = models.DateField(auto_now_add=True)
    expiry_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    allocated_by = models.ForeignKey('EmpManagement.emp_master', on_delete=models.SET_NULL, null=True, blank=True, related_name='allocated_tickets')
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.employee} - {self.amount} ({self.status})"
    
class AirticketNotification(models.Model):
    recipient_user     = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True,on_delete=models.CASCADE)
    recipient_employee = models.ForeignKey('EmpManagement.emp_master', null=True, blank=True, on_delete=models.CASCADE)
    message            = models.CharField(max_length=255)
    created_at         = models.DateTimeField(auto_now_add=True)
    is_read            = models.BooleanField(default=False)
    deligate_user      = models.ForeignKey('UserManagement.CustomUser',null=True,blank=True,on_delete=models.CASCADE,related_name='airticket_deligated_notifications')
    is_deligate        = models.BooleanField(default=False)

    def __str__(self):
        if self.recipient_user:
            return f"Notification for {self.recipient_user.username}: {self.message}"
        else:
            return f"Notification for employee: {self.message}"

class AirticketEmailTemplate(models.Model):
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


class AirTicketRequest(models.Model):
    REQUEST_STATUS = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('PROCESSED', 'Processed'),
    ]
    REQUEST_TYPE = [
        ('TICKET', 'Ticket'),
        ('ENCASHMENT', 'Encashment'),
    ]
    document_number = models.CharField(max_length=50, unique=True, null=True, blank=True)
    branch           =  models.ForeignKey('OrganisationManager.brnch_mstr',on_delete=models.SET_NULL, null=True)
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE,related_name='airticket_requests')
    allocation = models.ForeignKey(AirTicketAllocation, on_delete=models.CASCADE)
    request_type = models.CharField(max_length=20, choices=REQUEST_TYPE, default='TICKET')
    request_date = models.DateField()
    departure_date = models.DateField(null=True, blank=True)
    return_date = models.DateField(null=True, blank=True)
    origin = models.CharField(max_length=100, blank=True)
    destination = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=REQUEST_STATUS, default='PENDING')
    notes = models.TextField(blank=True)
    approved_by = models.ForeignKey('EmpManagement.emp_master', on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_tickets')
    approved_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by      = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')



    def __str__(self):
        return f"{self.employee} - {self.get_request_type_display()} ({self.get_status_display()})"
    def move_to_next_level(self):
        from django.utils import timezone
        from .utils import airticket_schedule_escalation

        # =========================================================
        # REJECT CHECK
        # =========================================================
        if self.approvals.filter(status=AirticketApproval.REJECTED).exists():
            self.status = 'REJECTED'
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                message=(f"Your AirticketRequest {self.request_type}"
                     f"(Document No: {self.document_number}) has been Rejected."
                    ),
                template_type="request_rejected",
                context={
                    **get_employee_context(self.employee),
                    'document_number': self.document_number,
                    'request_type': self.request_type,
                    'notes': self.notes,
                },
                email_template_model=AirticketEmailTemplate,
                notification_model=AirticketNotification
            )
            return

        # =========================================================
        # WORKFLOW
        # =========================================================
        workflow = AirticketApprovalWorkflow.objects.filter(
            branch__id=self.branch.id
        ).first()

        if not workflow:
            return

        approval_type = workflow.approval_type

        # =========================================================
        # 1. NO APPROVAL FLOW
        # =========================================================
        if approval_type == 'no_approval':
            self.status = 'APPROVED'
            self.approved_date = timezone.now()
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                message=(f"Your AirticketRequest {self.request_type}"
                     f"(Document No: {self.document_number}) has been AutoApproved."
                    ),
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    'document_number': self.document_number,
                    'request_type': self.request_type,
                },
                email_template_model=AirticketEmailTemplate,
                notification_model=AirticketNotification
            )
            return

        # =========================================================
        # 2. REPORTING MANAGER FLOW
        # =========================================================
        if approval_type == 'reporting_manager':

            reporting_manager = None
            if self.employee and self.employee.emp_reporting_manager:
                reporting_manager = self.employee.emp_reporting_manager

            # if no manager, auto approve
            if not reporting_manager:
                self.status = 'APPROVED'
                self.approved_date = timezone.now()
                self.save()

                send_notification_email(
                    user=self.created_by,
                    employee=self.employee,
                    message=(f"Your AirticketRequest {self.request_type}"
                     f"(Document No: {self.document_number}) has been Approved By ReportingManager."
                    ),
                    template_type="request_approved",
                    context={
                        **get_employee_context(self.employee),
                        'document_number': self.document_number,
                        'request_type': self.request_type,
                    },
                    email_template_model=AirticketEmailTemplate,
                    notification_model=AirticketNotification
                )
                return

            # create approval for manager
            new_approval = AirticketApproval.objects.create(
                request=self,
                approver=reporting_manager,
                role="Reporting Manager",
                level=1,
                status=AirticketApproval.PENDING,
                employee=self.employee
            )

            airticket_schedule_escalation(new_approval, None)

            send_notification_email(
                user=reporting_manager,
                employee=None,
                message=(f"Your AirticketRequest {self.request_type}"
                        f"(Document No: {self.document_number}) is waiting for your Approval."
                    ),
                template_type="request_created",
                context={
                    **get_employee_context(self.employee),
                    'document_number': self.document_number,
                    'request_type': self.request_type,
                },
                email_template_model=AirticketEmailTemplate,
                notification_model=AirticketNotification
            )
            return

        # =========================================================
        # 3. MULTI APPROVAL FLOW
        # =========================================================
        last_approved = self.approvals.filter(
            status=AirticketApproval.APPROVED
        ).order_by('-level').first()

        current_level = last_approved.level if last_approved else 0

        next_level = workflow.airticket_levels.filter(
            level__gt=current_level
        ).order_by('level').first()

        # ---------------- FINAL APPROVAL ----------------
        if not next_level:
            self.status = 'APPROVED'
            self.approved_date = timezone.now()
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                message=(f"Your AirticketRequest {self.request_type}"
                     f"(Document No: {self.document_number}) has been Approved."
                    ),
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    'document_number': self.document_number,
                    'request_type': self.request_type,
                },
                email_template_model=AirticketEmailTemplate,
                notification_model=AirticketNotification
            )
            return

        # ---------------- SAFETY CHECK ----------------
        if not next_level.approver:
            raise Exception(f"No approver configured for level {next_level.level}")

        # ---------------- NOTE CARRY ----------------
        last_approval = self.approvals.order_by('-level', '-id').first()

        note_to_carry = None
        if last_approval and last_approval.note:
            if not last_approval.note.startswith(("Escalated to", "Escalated from")):
                note_to_carry = last_approval.note

        # ---------------- CREATE NEXT APPROVAL ----------------
        new_approval = AirticketApproval.objects.create(
            request=self,
            approver=next_level.approver,
            role=next_level.role,
            level=next_level.level,
            status=AirticketApproval.PENDING,
            employee=self.employee,
            note=note_to_carry
        )

        # ---------------- ESCALATION ----------------
        airticket_schedule_escalation(new_approval, next_level)

        # ---------------- NOTIFICATION ----------------
        send_notification_email(
            user=next_level.approver,
            employee=None,
            message=(f"Your DocumentRequest {self.request_type}"
                     f"(Document No: {self.document_number}) is waiting for your Approval."
                    ),
            template_type="request_created",
            context={
                **get_employee_context(self.employee),
                'document_number': self.document_number,
                'request_type': self.request_type,
            },
            email_template_model=AirticketEmailTemplate,
            notification_model=AirticketNotification
        )

class AirticketApprovalWorkflow(models.Model):
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
    branch = models.ManyToManyField('OrganisationManager.brnch_mstr', blank=True)

    

class AirticketWorkflow(models.Model):
    workflow = models.ForeignKey(AirticketApprovalWorkflow,related_name='airticket_levels',on_delete=models.CASCADE)
    level = models.PositiveIntegerField()
    approver = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True)
    role = models.CharField(max_length=100,blank=True,null=True)
    escalate_to = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True, blank=True,related_name='airticket_escalated_levels')
    escalate_after_days = models.PositiveIntegerField(default=0, help_text="Escalate after X days if pending")
    escalate_after_hours = models.PositiveIntegerField(default=0, help_text="Escalate after X hours if pending")
    escalate_after_minutes = models.PositiveIntegerField(default=0, help_text="Escalate after X minutes if pending")

    def get_escalation_timedelta(self):
        """Returns the total time delta for escalation."""
        from datetime import timedelta
        total_minutes = (self.escalate_after_days * 24 * 60) + (self.escalate_after_hours * 60) + self.escalate_after_minutes
        return timedelta(minutes=total_minutes)
    class Meta:
        ordering = ['level']
        unique_together = ('workflow', 'level')
        permissions = (
                    ("add_airticket_escalation", "Can add Escalation"),
                    ("view_airticket_escalation", "Can view Escalation"),
                    ("change_airticket_escalation", "Can change Escalation"),
                    ("export_airticket_escalation", "Can export Escalation"),
                    ("delete_airticket_escalation", "Can delete Escalation"),
            )
    def __str__(self):
        return f"Level {self.level} - {self.role} ({self.approver})"
    
class AirticketApproval(models.Model):
    
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

    request = models.ForeignKey(AirTicketRequest, on_delete=models.CASCADE, related_name='approvals')
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE)
    approver = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True)
    role = models.CharField(max_length=100, null=True, blank=True)
    level = models.PositiveIntegerField()
    note = models.TextField(null=True, blank=True)
    deligate_to     = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,blank=True,related_name='airticket_deligations_received')
    is_deligate     = models.BooleanField(default=False)
    deligate_response = models.TextField(null=True, blank=True)
    escalated = models.BooleanField(default=False)
    escalated_at = models.DateTimeField(null=True, blank=True)
    is_escalation = models.BooleanField(default=False)
    rejection_reason     = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by      = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    updated_at = models.DateTimeField(auto_now=True)


    def __str__(self):
        return f"{self.request} - {self.approver} - {self.status}"

    def approve(self, note=None):
        # ✅ FIX: proper error type
        if self.status != self.PENDING:
            raise ValidationError({"detail": "This approval has already been processed."})

        self.status = self.APPROVED
        if note:
            self.note = note
        self.save()

        self.request.move_to_next_level()

    def reject(self, rejection_reason, note=None):
        # ✅ FIX: proper error type
        if self.status != self.PENDING:
            raise ValidationError({"detail": "This approval has already been processed."})

        self.status = self.REJECTED
        if note:
            self.note = note
        self.rejection_reason = rejection_reason   # ✅ store reason
        self.save()

        # ✅ FIX: correct field name (no 'remarks')
        self.request.status = 'REJECTED'
        self.request.notes = rejection_reason
        self.request.save()

        send_notification_email(
            user=self.request.created_by,
            employee=self.request.employee,
            message=(f"Your AirticketRequest {self.request_type}"
                     f"(Document No: {self.document_number}) has been Rejected."
                    ),
            template_type="request_rejected",
            context={
                **get_employee_context(self.request.employee),
                'document_number': self.request.document_number,
            },
            email_template_model=AirticketEmailTemplate,
            notification_model=AirticketNotification
        )
@receiver(post_save, sender=AirTicketRequest)
def create_initial_airticket_approval(sender, instance, created, **kwargs):

    if not created:
        return

    if not instance.branch:
        return

    from .utils import airticket_schedule_escalation

    with transaction.atomic():

        workflow = AirticketApprovalWorkflow.objects.filter(
            branch=instance.branch
        ).first()

        if not workflow:
            return

        approval_type = workflow.approval_type

        # ---------------- NO APPROVAL ----------------
        if approval_type == 'no_approval':

            approver = instance.created_by

            if not approver:
                raise Exception("Created_by user is missing.")

            AirticketApproval.objects.create(
                request=instance,
                approver=approver,
                role="Auto Approval",
                level=1,
                status=AirticketApproval.APPROVED,
                employee=instance.employee
            )

            AirTicketRequest.objects.filter(pk=instance.pk).update(
                status="APPROVED"
            )

            send_notification_email(
                user=approver,
                employee=instance.employee,
                branch=instance.branch,
                title="Air Ticket Auto Approved",
                notification_type="air_ticket",
                message=(f"Your AirticketRequest {instance.request_type}"
                     f"(Document No: {instance.document_number}) has been AutoApproved."
                    ),
                template_type="request_approved",
                context={
                    **get_employee_context(instance.employee),
                    "document_number": instance.document_number,
                    "request_type": instance.request_type,
                },
                email_template_model=AirticketEmailTemplate,
                notification_model=AirticketNotification,
            )

            return

        # ---------------- REPORTING MANAGER ----------------
        if approval_type == 'reporting_manager':

            manager = getattr(instance.employee, "emp_reporting_manager", None)

            if not manager:
                raise Exception("Employee has no reporting manager.")

            approval = AirticketApproval.objects.create(
                request=instance,
                approver=manager,
                role="Reporting Manager",
                level=1,
                status=AirticketApproval.PENDING,
                employee=instance.employee
            )

            send_notification_email(
                user=manager,
                employee=instance.employee,
                branch=instance.branch,
                title="Air Ticket Approval Request",
                notification_type="air_ticket",
                message=(f"Your AirticketRequest {instance.request_type}"
                     f"(Document No: {instance.document_number}) is waiting for your Approval."
                    ),
                template_type="request_created",
                context={
                    **get_employee_context(instance.employee),
                    "document_number": instance.document_number,
                    "request_type": instance.request_type,
                },
                email_template_model=AirticketEmailTemplate,
                notification_model=AirticketNotification,
            )

            return

        # ---------------- MULTI APPROVAL ----------------
        if approval_type == 'multi_approval':

            first_level = workflow.airticket_levels.order_by('level').first()

            if not first_level:
                raise Exception("No approval levels configured.")

            if not first_level.approver:
                raise Exception("First level approver is missing.")

            approval = AirticketApproval.objects.create(
                request=instance,
                approver=first_level.approver,
                role=first_level.role,
                level=first_level.level,
                status=AirticketApproval.PENDING,
                employee=instance.employee
            )

            airticket_schedule_escalation(approval, first_level)

            send_notification_email(
                user=first_level.approver,
                employee=instance.employee,
                branch=instance.branch,
                title="Air Ticket Approval Request",
                notification_type="air_ticket",
                message=(f"Your AirticketRequest {instance.request_type}"
                     f"(Document No: {instance.document_number}) is waiting for your Approval."
                    ),
                template_type="request_created",
                context={
                    **get_employee_context(instance.employee),
                    "document_number": instance.document_number,
                    "request_type": instance.request_type,
                },
                email_template_model=AirticketEmailTemplate,
                notification_model=AirticketNotification
                )
            return

class SalaryRevisionHistory(models.Model):
    employee = models.ForeignKey(
        'EmpManagement.emp_master', on_delete=models.CASCADE,
        related_name='salary_revisions_history'
    )
    component = models.ForeignKey(
        SalaryComponent, on_delete=models.CASCADE,
        related_name='revision_history'
    )
    old_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    new_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Ties the revision to a payroll period, like "April2026" in your Attendance Class dropdown
    effective_period = models.CharField(max_length=20, blank=True, null=True)

    created_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True)
       
    revised_on = models.DateTimeField(auto_now_add=True)
    remarks = models.CharField(max_length=255, blank=True, null=True)

    revisions = models.JSONField(default=list, blank=True)
    date_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'component')  # one row per employee+component

    def __str__(self):
        return f"{self.employee} - {self.component.name} ({len(self.revisions)} revisions)"

    def add_revision(self, old_amount, new_amount, revised_by=None, remarks='', effective_period=None):
        self.revisions.append({
            'old_amount': str(old_amount) if old_amount is not None else None,
            'new_amount': str(new_amount) if new_amount is not None else None,
            'revised_by': revised_by.get_full_name() if revised_by else None,
            'revised_by_id': revised_by.id if revised_by else None,
            'revised_on': timezone.now().isoformat(),
            'effective_period': effective_period,
            'remarks': remarks,
        })
        self.save(update_fields=['revisions', 'date_updated'])
        
@receiver(pre_save, sender=EmployeeSalaryStructure)
def track_salary_revision(sender, instance, **kwargs):
    if not instance.pk:
        return

    try:
        old_instance = EmployeeSalaryStructure.objects.get(pk=instance.pk)
    except EmployeeSalaryStructure.DoesNotExist:
        return

    if old_instance.amount != instance.amount:
        history, _ = SalaryRevisionHistory.objects.get_or_create(
            employee=instance.employee,
            component=instance.component,
        )
        history.add_revision(
            old_amount=old_instance.amount,
            new_amount=instance.amount,
            revised_by=getattr(instance, '_revised_by', None),
            remarks=getattr(instance, '_remarks', ''),
            effective_period=getattr(instance, '_effective_period', None),
        )
class LeaveEncashment(models.Model):

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("pending", "Pending Approval"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("processed", "Processed"),
        ("cancelled", "Cancelled"),
    ]

    employee = models.ForeignKey(
        'EmpManagement.emp_master',
        on_delete=models.PROTECT,
        related_name="leave_encashments"
    )

    leave_type = models.ForeignKey(
        "calendars.leave_type",
        on_delete=models.PROTECT,
        related_name="leave_encashments"
    )

    # Leave balance at the time of calculation/request
    leave_balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00")
    )

    # Number of days employee wants to encash
    encashment_days = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    # Salary snapshot used for calculation
    basic_salary = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00")
    )

    total_salary = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00")
    )

    fixed_days = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("30.00")
    )

    calendar_days = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("30.00")
    )

    # Formula snapshot
    formula_used = models.TextField(
        blank=True,
        null=True
    )

    # Final calculated amount
    encashment_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=Decimal("0.00")
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="draft"
    )

    remarks = models.TextField(
        blank=True,
        null=True
    )

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_leave_encashments"
    )

    approved_at = models.DateTimeField(
        null=True,
        blank=True
    )

    processed_at = models.DateTimeField(
        null=True,
        blank=True
    )

    # Optional payroll relationship
    payroll_run = models.ForeignKey(
        "PayrollManagement.PayrollRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leave_encashments"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"{self.employee.emp_code} - "
            f"{self.leave_type.name} - "
            f"{self.encashment_days} days"
        )