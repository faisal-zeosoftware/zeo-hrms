from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.utils import timezone
from datetime import datetime,timedelta
from EmpManagement .utils import send_notification_email,get_employee_context
# from calendars .models import LeaveApproval

# Create your models here.
class SalaryComponent(models.Model):
    COMPONENT_TYPES = [
        ('deduction', 'Deduction'),
        ('addition', 'Addition'),
        ('others', 'Others'),
    ]

    name = models.CharField(max_length=100)  # Component name (e.g., HRA, PF)
    component_type = models.CharField(max_length=20, choices=COMPONENT_TYPES)
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', on_delete=models.CASCADE,null=True,blank=True, related_name='salary_components')
    code = models.CharField(max_length=20,null=True)
    deduct_leave=models.BooleanField(default=False)
    is_fixed = models.BooleanField(default=True, help_text="Is this component fixed (True) or variable (False)?")
    formula = models.CharField(max_length=255, blank=True, null=True, help_text="Formula to calculate this component (e.g., 'basic_salary * 0.4')")
    description = models.TextField(blank=True, null=True)
    is_loan_component = models.BooleanField(default=False, help_text="Mark if this is the loan deduction component")
    show_in_payslip = models.BooleanField(default=True, help_text="Should this component be shown on the payslip?")
    is_advance_salary = models.BooleanField(default=False, help_text="Used for advance salary deductions")
    is_air_ticket = models.BooleanField(default=False, help_text="Used for air ticket")
    is_gratuity = models.BooleanField(default=False, help_text="Used for emp-gratuity")
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['name', 'branch'], name='unique_salary_component_name_per_branch'),
            models.UniqueConstraint(fields=['code', 'branch'], name='unique_salary_component_code_per_branch'),
        ]
    def __str__(self):
        return f"{self.name} ({self.get_component_type_display()})"

class EmployeeSalaryStructure(models.Model):
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE, related_name='salary_structures')
    component = models.ForeignKey(SalaryComponent, on_delete=models.CASCADE, related_name='employee_components')
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Amount for this component")
    is_active = models.BooleanField(default=True, help_text="Is this component active for the employee?")
    date_created = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'component')  # Ensure no duplicate components for an employee

    def __str__(self):
        return f"{self.employee} - {self.component.name} ({self.amount})"


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
    
    name = models.CharField(max_length=100, blank=True, help_text="Optional payroll run name")
    month = models.IntegerField(choices=MONTH_CHOICES, help_text="Month of the payroll period")
    year = models.IntegerField(help_text="Year of the payroll period")
    payment_date = models.DateField(null=True, blank=True, help_text="When employees will be paid")
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', on_delete=models.SET_NULL, null=True, blank=True)
    department = models.ForeignKey('OrganisationManager.dept_master', on_delete=models.SET_NULL, null=True, blank=True)
    category = models.ForeignKey('OrganisationManager.ctgry_master', on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def get_employees(self):
        from EmpManagement.models import emp_master
        try:
            employees = emp_master.objects.all()
            
            if self.branch:
                employees = employees.filter(emp_branch_id=self.branch)
            if self.department:
                employees = employees.filter(emp_dept_id=self.department)
            if self.category:
                employees = employees.filter(emp_ctgry_id=self.category)
                
            return employees
        except Exception as e:
            return emp_master.objects.none()

    def get_month_display(self):
        return dict(self.MONTH_CHOICES).get(self.month, 'Unknown')

    def __str__(self):
        return f"Payroll - {self.get_month_display()} {self.year} ({self.status})"

    class Meta:
        unique_together = ('month', 'year', 'branch', 'department', 'category')

class Payslip(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processed', 'Processed'),
        ('paid', 'Paid'),
        ('rejected', 'Rejected'),
        ('Approved', 'Approved'),  
    ]
    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name='payslips')
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE, related_name='payslips')
    gross_salary = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)  # Added
    net_salary = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    total_deductions = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_additions = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    # New fields for working days
    total_working_days = models.PositiveIntegerField(default=0, help_text="Total working days in the payroll period")
    days_worked = models.PositiveIntegerField(default=0, help_text="Number of days the employee worked")
    pro_rata_adjustment = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Pro-rata adjustment")  # New field
    arrears = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Arrears amount")  # New field
    send_email = models.BooleanField(default=False, help_text="Send this payslip via email if True")
    payslip_pdf = models.FileField(upload_to='payslips/',null=True,blank=True,validators=[FileExtensionValidator(allowed_extensions=['pdf'])],)
    confirm_status = models.BooleanField(default=False, help_text="confirm this payslip  if True")
    trial_status = models.BooleanField(default=False, help_text="confirm this payslip  if True")
    rejection_reason = models.TextField(null=True, blank=True)
    currency = models.ForeignKey("Core.crncy_mstr", on_delete=models.SET_NULL, null=True, blank=True)
    def save(self, *args, **kwargs):
        if not self.currency and self.employee and self.employee.emp_country_id:
            if hasattr(self.employee.emp_country_id, 'currency'):
                self.currency = self.employee.emp_country_id.currency
        super().save(*args, **kwargs)
    def move_to_next_level(self):
        """
        Moves to the next approval level only if all current levels are approved.
        Stops if any level was rejected.
        """
        if self.approvals.filter(status='Rejected').exists():
            self.status = 'Rejected'
            self.save()
            return

        current_approved_levels = self.approvals.filter(status='Approved').count()
        next_level = PayslipCommonWorkflow.objects.filter(level=current_approved_levels + 1).first()

        if next_level:
            PayslipApproval.objects.create(
                request=self,
                approver=next_level.approver,
                role=next_level.role,
                level=next_level.level,
                status='Pending',
                employee=self.employee
            )
        else:
            self.status = 'Approved'
            self.approval_date = timezone.now()
            self.save()
class PayslipCommonWorkflow(models.Model):
    level = models.PositiveIntegerField()
    approver = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True)
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

    request = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name='approvals')
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE)
    approver = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True)
    role = models.CharField(max_length=100, null=True, blank=True)
    level = models.PositiveIntegerField()
    note = models.TextField(null=True, blank=True)
    rejection_reason     = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    created_at = models.DateTimeField(auto_now_add=True)

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
@receiver(post_save, sender=Payslip)
def create_initial_advance_approval(sender, instance, created, **kwargs):
    if created:
        first_level = PayslipCommonWorkflow.objects.order_by('level').first()
        if first_level:
            PayslipApproval.objects.create(
                request=instance,
                approver=first_level.approver,
                role=first_level.role,
                level=first_level.level,
                status='Pending',
                employee=instance.employee
            )
class PayslipComponent(models.Model):
    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name='components')
    component = models.ForeignKey(SalaryComponent, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    def __str__(self):
        return f"{self.payslip.employee} - {self.component.name} ({self.amount})"

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
    def __str__(self):
        return f"{self.template_type} - {self.subject}"
class LoanNotification(models.Model):
    recipient_user = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True, on_delete=models.CASCADE)
    recipient_employee = models.ForeignKey('EmpManagement.emp_master', null=True, blank=True, on_delete=models.CASCADE, related_name='loan_notification')
    message = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    def __str__(self):
        if self.recipient_user:
            return f"Notification for {self.recipient_user.emp_code}: {self.message}"
        else:
            return f"Notification for employee: {self.message}"

class LoanType(models.Model):
    loan_type           = models.CharField(max_length=255)  # e.g., Personal, Housing, Car
    max_amount          = models.DecimalField(max_digits=10, decimal_places=2)
    repayment_period    = models.PositiveIntegerField()  # in months
    created_at          = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)
    use_common_workflow = models.BooleanField(default=False)
    created_by          = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')


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
        if self.approvals.filter(status=LoanApproval.REJECTED).exists():
            self.status = 'Rejected'
            self.save()
            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                message=f"Your request {self.loan_type} has been rejected.",
                template_type="request_rejected",
                context={
                    **get_employee_context(self.employee),
                    'doc_number': self.loan_type,
                    'loan_type': self.loan_type.loan_type,
                    
                },
                email_template_model=LoanEmailTemplate,
                notification_model=LoanNotification
            )
            return  # Important: Stop here if rejected
            
        current_approved_levels = self.approvals.filter(status=LoanApproval.APPROVED).count()

        if self.loan_type.use_common_workflow:
            next_level = LoanCommonWorkflow.objects.filter(level=current_approved_levels + 1).first()
        else:
            next_level = LoanApprovalLevels.objects.filter(loan_type=self.loan_type, level=current_approved_levels + 1).first()

        if next_level:
            last_approval = self.approvals.order_by('-level').first()
            LoanApproval.objects.create(
                loan_request=self,
                approver=next_level.approver,
                role=next_level.role,
                level=next_level.level,
                status=LoanApproval.PENDING,
                note=last_approval.note if last_approval else None
            )
            send_notification_email(
                user=next_level.approver,
                employee=None,
                message=f"New request for approval: {self.loan_type.loan_type}, Employee: {self.employee}",
                template_type="request_created",
                context={
                    **get_employee_context(self.employee),
                    'loan_type': self.loan_type.loan_type,
                    
                },
                email_template_model=LoanEmailTemplate,
                notification_model=LoanNotification
            )
            
        else:
            self.status = 'Approved'
            self.save()
            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                message=f"Your request {self.loan_type.loan_type} has been approved.",
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    'loan_type': self.loan_type.loan_type,
                
                },
                email_template_model=LoanEmailTemplate,
                notification_model=LoanNotification
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

class LoanApprovalLevels(models.Model):
    level            = models.IntegerField()
    role             = models.CharField(max_length=50, null=True, blank=True)  # Use this for role-based approval like 'CEO' or 'Manager'
    approver         = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True, on_delete=models.SET_NULL)  # Use this for user-based approval
    loan_type        = models.ForeignKey('LoanType', related_name='loan_approval_levels', on_delete=models.CASCADE, null=True, blank=True)  # Nullable for common workflow
    class Meta:
        unique_together = ('level', 'loan_type')

class LoanApproval(models.Model):
    PENDING = 'Pending'
    APPROVED = 'Approved'
    REJECTED = 'Rejected'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (APPROVED, 'Approved'),
        (REJECTED, 'Rejected'),
    ]
    loan_request         = models.ForeignKey(LoanApplication, related_name='approvals', on_delete=models.CASCADE,null=True, blank=True)
    approver             = models.ForeignKey('UserManagement.CustomUser', on_delete=models.CASCADE)
    role                 = models.CharField(max_length=50, null=True, blank=True)
    level                = models.IntegerField(default=1)
    status               = models.CharField(max_length=20, choices=STATUS_CHOICES,default=PENDING)
    note                 = models.TextField(null=True, blank=True)
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
        message=f"Your request {self.loan_request.loan_type} has been rejected.",
        template_type="request_rejected",
        context={
            **get_employee_context(self.loan_request.employee),
            'loan_type': self.loan_request.loan_type.loan_type,
            
        },
        email_template_model=LoanEmailTemplate,
        notification_model=LoanNotification
    )
    
        
@receiver(post_save, sender=LoanApplication)
def create_initial_approval(sender, instance, created, **kwargs):
    if created:
        if instance.loan_type.use_common_workflow:
            first_level = LoanCommonWorkflow.objects.order_by('level').first()
        else:
        # Select the first approval level
            first_level = LoanApprovalLevels.objects.filter(loan_type=instance.loan_type).order_by('level').first()

        if first_level:
            # Prevent duplicate creation of approvals at the same level
            if not instance.approvals.filter(level=first_level.level).exists():
                LoanApproval.objects.create(
                    loan_request=instance,
                    approver=first_level.approver,
                    role=first_level.role,
                    level=first_level.level,
                    status=LoanApproval.PENDING,
                    employee_id=instance.employee_id
                )
                send_notification_email(
                user=first_level.approver,
                employee=None,
                message=f"New request for approval: {instance.loan_type.loan_type}, Employee: {instance.employee}",
                template_type="request_created",
                context={
                    **get_employee_context(instance.employee),
                    'loan_type': instance.loan_type.loan_type,
                    
                },
                email_template_model=LoanEmailTemplate,
                notification_model=LoanNotification
            )
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
    def __str__(self):
        return f"{self.template_type} - {self.subject}"
class AdvanceSalaryNotification(models.Model):
    recipient_user = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True, on_delete=models.CASCADE)
    recipient_employee = models.ForeignKey('EmpManagement.emp_master', null=True, blank=True, on_delete=models.CASCADE, related_name='advance_salary_notification')
    message = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

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
        """
        Moves to the next approval level only if all current levels are approved.
        Stops if any level was rejected.
        """
        if self.approvals.filter(status='Rejected').exists():
            self.status = 'Rejected'
            self.save()
            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                message=f"Your request {self.document_number} has been rejected.",
                template_type="request_rejected",
                context={
                    **get_employee_context(self.employee),
                    'doc_number': self.document_number,
                    
                },
                email_template_model=AdvanceSalaryEmailTemplate,
                notification_model=AdvanceSalaryNotification
            )
            return  # Important: Stop here if rejected

        current_approved_levels = self.approvals.filter(status='Approved').count()
        next_level = AdvanceCommonWorkflow.objects.filter(level=current_approved_levels + 1).first()

        if next_level:
            AdvanceSalaryApproval.objects.create(
                request=self,
                approver=next_level.approver,
                role=next_level.role,
                level=next_level.level,
                status='Pending',
                employee=self.employee
            )
            send_notification_email(
                user=next_level.approver,
                employee=None,
                message=f"New Salary Advance request for approval: {self.document_number}, Employee: {self.employee}",
                template_type="request_created",
                context={
                    **get_employee_context(self.employee),
                    'document_number': self.document_number,
                    
                },
                email_template_model=AdvanceSalaryEmailTemplate,
                notification_model=AdvanceSalaryNotification
            )
        else:
            self.status = 'Approved'
            self.approval_date = timezone.now()
            self.save()
            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                message=f"Your request {self.document_number} has been approved.",
                template_type="request_approved",
                context={
                    **get_employee_context(self.employee),
                    'doc_number': self.document_number,
                },
                email_template_model=AdvanceSalaryEmailTemplate,
                notification_model=AdvanceSalaryNotification
            )
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

class AdvanceCommonWorkflow(models.Model):
    level = models.PositiveIntegerField()
    approver = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True)
    role = models.CharField(max_length=100)

    class Meta:
        ordering = ['level']

    def __str__(self):
        return f"Level {self.level} - {self.role} ({self.approver})"
class AdvanceSalaryApproval(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
    ]

    request = models.ForeignKey(AdvanceSalaryRequest, on_delete=models.CASCADE, related_name='approvals')
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE)
    approver = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True)
    role = models.CharField(max_length=100, null=True, blank=True)
    level = models.PositiveIntegerField()
    note = models.TextField(null=True, blank=True)
    rejection_reason     = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    created_at = models.DateTimeField(auto_now_add=True)

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
        message=f"Your Advance salary  request {self.request} has been rejected.",
        template_type="request_rejected",
        context={
            **get_employee_context(self.request.employee),
            'loan_type': self.request,
            
        },
        email_template_model=LoanEmailTemplate,
        notification_model=LoanNotification
    )
@receiver(post_save, sender=AdvanceSalaryRequest)
def create_initial_advance_approval(sender, instance, created, **kwargs):
    if created:
        first_level = AdvanceCommonWorkflow.objects.order_by('level').first()
        if first_level:
            AdvanceSalaryApproval.objects.create(
                request=instance,
                approver=first_level.approver,
                role=first_level.role,
                level=first_level.level,
                status='Pending',
                employee=instance.employee
            )

            send_notification_email(
            user=first_level.approver,
            employee=None,
            message=f"New request for approval: {instance.document_number}, Employee: {instance.employee}",
            template_type="request_created",
            context={
                **get_employee_context(instance.employee),
                'document_number': instance.document_number,
            },
            email_template_model=AdvanceSalaryEmailTemplate,
            notification_model=AdvanceSalaryNotification
    )            

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
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE)
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


    def __str__(self):
        return f"{self.employee} - {self.get_request_type_display()} ({self.get_status_display()})"