from django.db import models
from EmpManagement.models import emp_master
from datetime import datetime, timedelta
from EmpManagement .models import Emp_CustomField
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q,F
from django.dispatch import receiver
from EmpManagement.utils import send_notification_email, get_employee_context
from  .utils import asset_schedule_escalation
from EmpManagement.models import RequestNotification
from django.db.models.signals import post_save

#branch model
class brnch_mstr(models.Model):
    branch_name               = models.CharField(max_length=100)
    branch_code               = models.CharField(max_length=50,unique=True)
    branch_logo               = models.ImageField(null=True)
    probation_period_days     = models.IntegerField(default=0)
    br_start_date             = models.DateField(null=True)
    # branch_users              = models.ManyToManyField("UserManagement.CustomUser",related_name='branches')
    br_is_active              = models.BooleanField(default=True)
    br_state_id               = models.ForeignKey("Core.state_mstr",on_delete=models.CASCADE,null=True)  
    br_city                   = models.CharField(max_length=50)
    br_pincode                = models.CharField(max_length=20)
    br_branch_nmbr_1          = models.CharField(max_length=20,unique=True)
    br_branch_nmbr_2          = models.CharField(max_length=20,blank=True, null=True)
    br_branch_mail            = models.EmailField()
    br_country                = models.ForeignKey("Core.cntry_mstr",on_delete=models.SET_DEFAULT, default="1", null=True) 
    br_created_at             = models.DateTimeField(auto_now_add=True)
    br_created_by             = models.ForeignKey('UserManagement.CustomUser', on_delete=models.CASCADE, null=True, related_name='%(class)s_created_by')
    br_updated_at             = models.DateTimeField(auto_now=True)
    br_updated_by             = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_updated_by')
    def __str__(self):
        return self.branch_name
    def delete(self, *args, **kwargs):
        from django.apps import apps
        from django.core.exceptions import ValidationError

        # List of models that represent "transactions" or active associations with a branch.
        # Format: (app_label, model_name, field_name_on_that_model)
        transaction_checks = [
            ('EmpManagement', 'emp_master', 'emp_branch_id'),
            ('EmpManagement', 'GeneralRequest', 'branch'),
            ('calendars', 'employee_leave_request', 'branch'),
            ('OrganisationManager', 'AssetRequest', 'branch'),
            ('PayrollManagement', 'PayrollRun', 'branch'),
            ('PayrollManagement', 'LoanApplication', 'branch'),
            ('PayrollManagement', 'AdvanceSalaryRequest', 'branch'),
            ('PayrollManagement', 'AirTicketRequest', 'branch'),
            ('calendars', 'Attendance', 'employee__emp_branch_id'),
            ('OrganisationManager', 'FiscalYear', 'branch_id'),
            ('OrganisationManager', 'DocumentNumbering', 'branch_id'),
        ]

        for app_label, model_name, field_name in transaction_checks:
            try:
                Model = apps.get_model(app_label, model_name)
                if Model.objects.filter(**{field_name: self}).exists():
                    readable_name = model_name.replace('_', ' ').capitalize()
                    raise ValidationError(
                        f"This branch cannot be deleted because it is associated with existing transactions in '{readable_name}'. "
                        "Please deactivate the branch instead of deleting it to preserve data integrity."
                    )
            except (LookupError, ValueError):
                # Skip if the app or model is not available in the current environment
                continue

        super().delete(*args, **kwargs)

#departments model
class dept_master(models.Model):
    dept_name        = models.CharField(max_length=50)
    dept_code        = models.CharField(max_length=50,unique=True,null=True,blank=True)
    dept_description = models.CharField(max_length=200,null=True,blank=True)
    dept_created_at  = models.DateTimeField(auto_now_add=True)
    dept_created_by  = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    dept_updated_at  = models.DateTimeField(auto_now=True)
    dept_updated_by  = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_updated_by')
    dept_is_active   = models.BooleanField(default=True)
    branch = models.ManyToManyField("brnch_mstr", blank=True, related_name='dept_branches')
    class Meta:
        permissions = (
                # ("add_dept_report", "Can add department report"),
                ("view_dept_report", "Can view department report"),
                ("export_dept_report", "Can export department report"),
                ("delete_dept_report", "Can delete department report"),
                ("import_dept_master", "Can import department master"),
        )
    # Method to fetch all department users
    def get_department_users(self):
        # You can customize this to fetch relevant users within the department
        return 'UserManagement.CustomUser'.objects.filter(department=self, is_active=True)


    def __str__(self):
        return self.dept_name

#designation master
class desgntn_master(models.Model):
    desgntn_job_title   =  models.CharField(max_length=50)
    desgntn_code        = models.CharField(max_length=50,unique=True,null=True,blank=True)
    desgntn_description = models.CharField(max_length=200,null=True,blank=True)
    desgntn_created_at  = models.DateTimeField(auto_now_add=True)
    desgntn_created_by  = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    desgntn_updated_at  = models.DateTimeField(auto_now=True)
    desgntn_updated_by  = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_updated_by')
    desgntn_is_active   = models.BooleanField(default=True)
    branch              = models.ManyToManyField("brnch_mstr", blank=True, related_name='desgntn_branches')
    
    class Meta:
        permissions = (
                # ("add_designtn_report", "Can add designation report"),
                ("view_designtn_report", "Can view designation report"),
                ("export_designtn_report", "Can export designation report"),
                ("delete_designtn_report", "Can delete designation report"),
                ("import_designtn_master", "Can import department master"),
        )
    def __str__(self):
        return self.desgntn_job_title

#CATOGARY master
class ctgry_master(models.Model):
    ctgry_title       =  models.CharField(max_length=50)
    ctgry_code        = models.CharField(max_length=50,unique=True,null=True,blank=True)    
    ctgry_description = models.CharField(max_length=200,null=True,blank=True)
    ctgry_created_at  = models.DateTimeField(auto_now_add=True)
    ctgry_created_by  = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    ctgry_updated_at  = models.DateTimeField(auto_now=True)
    ctgry_updated_by  = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_updated_by')
    ctgry_is_active   = models.BooleanField(default=True)
    branch = models.ManyToManyField("brnch_mstr", blank=True, related_name='ctgry_branches')
    class Meta:
        permissions = (
                ("import_ctgry_master", "Can import category master"),
        )
    
    def __str__(self):
        return self.ctgry_title

class FiscalYear(models.Model):
    branch_id  = models.ForeignKey("brnch_mstr",on_delete=models.CASCADE)
    name       = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date   = models.DateField(blank=True,null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')


class FiscalPeriod(models.Model):
    fiscal_year   = models.ForeignKey(FiscalYear, on_delete=models.CASCADE)
    period_number = models.PositiveIntegerField()
    start_date    = models.DateField()
    end_date      = models.DateField()
    branch        = models.ForeignKey("brnch_mstr", on_delete=models.CASCADE, related_name='fiscal_periods')
    class Meta:
        unique_together = ('fiscal_year', 'period_number')

class DocumentNumbering(models.Model):
    DOCUMENT_TYPES = [
        ('general_request', 'General Request'),
        ('leave_request', 'Leave Request'),
        ('advance_salary_request', 'Advance Salary Request'),
        ('air_ticket_request', 'Air Ticket Request'),
        ('loan_request', 'Loan Request'),
        ('asset_request','Asset Request'),
        ('document_request','Document Request'),
        ('resignation_request','Resignation Request')


    ]

    branch_id = models.ForeignKey('brnch_mstr', on_delete=models.CASCADE)
    type = models.CharField(max_length=50, choices=DOCUMENT_TYPES)
    user = models.ForeignKey('UserManagement.CustomUser', on_delete=models.CASCADE,null=True,blank=True,related_name='document_numbering_user')

    # leave_type = models.ForeignKey('calendars.leave_type', on_delete=models.CASCADE, null=True, blank=True)

    automatic_numbering = models.BooleanField(default=True)
    prefix = models.CharField(max_length=50)
    suffix = models.CharField(max_length=50, blank=True, null=True)
    # year = models.IntegerField(default=timezone.now().year)
    current_number = models.IntegerField(default=0)  # Tracks the last used number
    total_length = models.IntegerField(default=10)  # Total length of the document number
    start_date = models.DateField(blank=True, null=True)  # New field
    end_date = models.DateField(blank=True, null=True)  # New field
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True,related_name='document_numbering_created_by')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['branch_id', 'type'],name='unique_type_per_branch'),]
    def __str__(self):
        return f"{self.branch_id.branch_name} - {self.type}"

    def clean(self):
        if DocumentNumbering.objects.filter(
            branch_id=self.branch_id,
            type=self.type
        ).exclude(id=self.id).exists():
            raise ValidationError("A document numbering already exists for this branch and type.")
        if self.start_date and self.end_date:
            if self.start_date >= self.end_date:
                raise ValidationError({'end_date': "End date must be greater than start date."})

        prefix = self.prefix or ''
        suffix = self.suffix or ''

        if self.total_length < len(prefix) + len(suffix) + 1:
            raise ValidationError({
                'total_length': "Total length is too short for the given prefix and suffix."
            })
            
    def get_next_number(self):
        """Generate the next document number with a fixed total length, without using the year field."""
        current_date = timezone.now().date()
        if self.start_date and self.end_date:
            if not (self.start_date <= current_date <= self.end_date):
                raise ValidationError("Document number cannot be generated outside the valid date range.")

        with transaction.atomic():
            doc_numbering = DocumentNumbering.objects.select_for_update().get(id=self.id)

            prefix = doc_numbering.prefix or ''
            suffix = doc_numbering.suffix or ''

            # FIX: base = prefix + suffix (NO dash here)
            base_text = f"{prefix}{suffix}"

            # only one dash before number (as per your required format)
            available_space = doc_numbering.total_length - len(base_text) - 1

            if available_space < 1:
                raise ValidationError("Invalid total length configuration.")

            next_number = doc_numbering.current_number + 1

            max_limit = int("9" * available_space)
            if next_number > max_limit:
                raise ValidationError("Document number limit reached.")

            number_part = str(next_number).zfill(available_space)
            final_number = f"{prefix}{suffix}-{number_part}"

            # save after validation
            doc_numbering.current_number = next_number
            doc_numbering.save()

            return final_number
    

class CompanyPolicy(models.Model):
    title           = models.CharField(max_length=200)
    description     = models.TextField()
    policy_file     = models.FileField(upload_to='policies/')
    branch          = models.ForeignKey('brnch_mstr',on_delete=models.CASCADE, related_name='policies')
    department      = models.ForeignKey('dept_master', on_delete=models.CASCADE, related_name='policies', blank=True, null=True)
    category        = models.ForeignKey('ctgry_master', on_delete=models.CASCADE, related_name='policies', blank=True, null=True)
    # specific_employees = models.ManyToManyField(emp_master, blank=True)
    specific_users  = models.ManyToManyField('UserManagement.CustomUser', blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)
    created_by     = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    def __str__(self):
        return self.title

class Announcement(models.Model):
    title = models.CharField(max_length=255)
    message = models.TextField()
    send_email = models.BooleanField(default=True)
    is_sticky = models.BooleanField(default=False)
    schedule_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    specific_employees = models.ManyToManyField(emp_master, blank=True, related_name='employee_announcements')
    branches = models.ManyToManyField(brnch_mstr, blank=True, related_name='branch_announcements')
    emp_dept_id = models.ManyToManyField(dept_master,null=True,blank =True)
    emp_desgntn_id = models.ManyToManyField(desgntn_master,null=True,blank =True)
    emp_ctgry_id = models.ManyToManyField(ctgry_master,null=True,blank =True)
    attachment = models.FileField(upload_to='announcements/', null=True, blank=True)
    allow_comments = models.BooleanField(default=True)
    created_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_expired(self):
        return self.expires_at and self.expires_at < timezone.now()

    def __str__(self):
        return self.title
class AnnouncementView(models.Model):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name='announcement_views')
    employee = models.ForeignKey(emp_master, on_delete=models.CASCADE)
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('announcement', 'employee')  # Avoid duplicate views


class AnnouncementComment(models.Model):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name='announcement_comments')
    employee = models.ForeignKey(emp_master, on_delete=models.CASCADE)
    comment = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Comment by {self.employee} on {self.announcement}"

class AssetEmailTemplate(models.Model):
    template_type = models.CharField(max_length=50, choices=[
        ('asset_created', 'Asset Created'),
        ('asset_approved', 'Asset Approved'),
        ('asset_rejected', 'Asset Rejected')
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

class AssetType(models.Model):
    name        = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    min_approvals_required = models.PositiveIntegerField(default=1, help_text="Minimum number of approvals required to complete the request")
    branch = models.ManyToManyField('OrganisationManager.brnch_mstr',blank=True)

    def __str__(self):
        return self.name


class Asset(models.Model):
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('assigned', 'Assigned'),
        ('maintenance', 'Under Maintenance'),
        ('disposed', 'Disposed'),
    ]

    CONDITION_CHOICES = [
        ('healthy', 'Healthy'),
        ('minor_damage', 'Minor Damage'),
        ('major_damage', 'Major Damage'),
    ]

    asset_type     = models.ForeignKey(AssetType, on_delete=models.CASCADE, related_name="assets")
    name           = models.CharField(max_length=100)
    serial_number  = models.CharField(max_length=100, unique=True)
    model = models.CharField(max_length=100, blank=True)
    purchase_date  = models.DateField()
    # warranty_until = models.DateField(null=True, blank=True)
    status         = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')
    condition      = models.CharField(max_length=20, choices=CONDITION_CHOICES, default='healthy')

    def __str__(self):
        return self.name

class AssetRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    employee          = models.ForeignKey(emp_master, on_delete=models.CASCADE, related_name="asset_requests")
    asset_type        = models.ForeignKey(AssetType,on_delete = models.CASCADE)
    requested_asset   = models.ForeignKey(Asset, on_delete=models.SET_NULL, null=True, blank=True)
    reason            = models.TextField()
    status            = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    # request_date    = models.DateField(auto_now_add=True)
    request_date      = models.DateTimeField(auto_now=True)
    created_by        = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True)
    document_number   = models.CharField(max_length=50, unique=True, blank=True)
    branch           =  models.ForeignKey('OrganisationManager.brnch_mstr',on_delete=models.SET_NULL, null=True)


    def __str__(self):
        return f"{self.document_number}-{self.asset_type.name}"

    def get_employee_requests(employee_id):
        return AssetRequest.objects.filter(employee_id=employee_id).order_by('-created_at_date')
    
    def move_to_next_level(self):
        from django.utils import timezone
        from django.db.models import Max

        # ---------------- REJECT CHECK ---------------- #
        if self.approvals.filter(status=AssetApproval.REJECTED).exists():
            self.status = 'Rejected'
            self.save()

            send_notification_email(
                user=self.created_by,
                employee=self.employee,
                message=f"Your request {self.document_number} has been rejected.",
                template_type="asset_rejected",
                context={
                    **get_employee_context(self.employee),
                    'asset_type': self.asset_type.name,
                    'requested_asset': self.requested_asset.name if self.requested_asset else None,
                    'request_date': self.request_date,
                    'reason': self.reason,
                },
                email_template_model=AssetEmailTemplate,
                notification_model=RequestNotification,
            )
            return

        # ---------------- CURRENT APPROVED LEVEL ---------------- #
        current_level = self.approvals.filter(
            status=AssetApproval.APPROVED
        ).aggregate(
            max_level=Max('level')
        )['max_level'] or 0

        next_level_number = current_level + 1

        # ---------------- NEXT LEVEL ---------------- #
        next_level = AssetApprovalLevel.objects.filter(
            workflow__asset_type=self.asset_type,
            workflow__branch=self.employee.emp_branch_id,
            level=next_level_number
        ).first()

        # ---------------- FALLBACK ---------------- #
        if not next_level:
            next_level = AssetApprovalLevel.objects.filter(
                workflow__asset_type=self.asset_type,
                level=next_level_number
            ).first()

        # ---------------- FINAL APPROVAL ---------------- #
        if not next_level:

            # check pending approvals
            pending_exists = self.approvals.filter(
                status=AssetApproval.PENDING
            ).exists()

            if not pending_exists:
                self.status = 'Approved'
                self.save()

                if self.requested_asset:
                    AssetAllocation.objects.create(
                        asset=self.requested_asset,
                        employee=self.employee,
                        assigned_date=timezone.now().date()
                    )

                    self.requested_asset.status = "assigned"
                    self.requested_asset.save()

                send_notification_email(
                    user=self.created_by,
                    employee=self.employee,
                    message=f"Your request {self.document_number} has been approved.",
                    template_type="asset_approved",
                    context={
                        **get_employee_context(self.employee),
                        'asset_type': self.asset_type.name,
                        'requested_asset': self.requested_asset.name if self.requested_asset else None,
                        'request_date': self.request_date,
                        'reason': self.reason,
                    },
                    email_template_model=AssetEmailTemplate,
                    notification_model=RequestNotification,
                )

            return

        # ---------------- CHECK EXISTING NEXT LEVEL ---------------- #
        existing_next = self.approvals.filter(
            level=next_level.level
        ).first()

        if existing_next:
            return

        # ---------------- LAST APPROVAL NOTE ---------------- #
        last_approval = self.approvals.order_by('-level', '-id').first()

        note_to_carry = None

        if last_approval and last_approval.note:
            if not last_approval.note.startswith(
                ("Escalated to", "Escalated from")
            ):
                note_to_carry = last_approval.note

        # ---------------- SAFETY ---------------- #
        if not next_level.approver:
            self.status = 'Approved'
            self.save()
            return

        # ---------------- CREATE NEXT APPROVAL ---------------- #
        approval = AssetApproval.objects.create(
            asset_request=self,
            approver=next_level.approver,
            role=next_level.role,
            level=next_level.level,
            status=AssetApproval.PENDING,
            created_by=self.created_by,
            note=note_to_carry
        )

        # ---------------- ESCALATION ---------------- #
        asset_schedule_escalation(approval, next_level)

        # ---------------- NOTIFICATION ---------------- #
        send_notification_email(
            user=next_level.approver,
            employee=None,
            message=f"New asset request: {self.document_number}",
            template_type="asset_created",
            context={
                **get_employee_context(self.employee),
                'asset_type': self.asset_type.name,
                'requested_asset': self.requested_asset.name if self.requested_asset else None,
                'request_date': self.request_date,
                'reason': self.reason,
            },
            email_template_model=AssetEmailTemplate,
            notification_model=RequestNotification,
        )

        self.status = 'Pending'
        self.save()

class AssetApprovalWorkflow(models.Model):
    asset_type = models.ForeignKey('AssetType', related_name='approval_levels', on_delete=models.CASCADE, null=True, blank=True)  # Nullable for common workflow
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



class AssetApprovalLevel(models.Model):
    workflow = models.ForeignKey(AssetApprovalWorkflow, related_name='asset_levels', on_delete=models.CASCADE, null=True)
    level = models.IntegerField()
    role = models.CharField(max_length=50, null=True, blank=True)  # Use this for role-based approval like 'CEO' or 'Manager'
    approver = models.ForeignKey('UserManagement.CustomUser', null=True, blank=True, on_delete=models.SET_NULL)  # Use this for user-based approval
      # 🆕 Escalation fields
    escalate_to = models.ForeignKey('UserManagement.CustomUser',on_delete=models.SET_NULL,null=True, blank=True,related_name='asset_escalated_levels')
    escalate_after_days = models.PositiveIntegerField(default=0, help_text="Escalate after X days if pending")
    escalate_after_hours = models.PositiveIntegerField(default=0, help_text="Escalate after X hours if pending")
    escalate_after_minutes = models.PositiveIntegerField(default=0, help_text="Escalate after X minutes if pending")
    class Meta:
        permissions = (
                ("add_asset_escalation", "Can add Escalation"),
                ("view_asset_escalation", "Can view Escalation"),
                ("change_asset_escalation", "Can change Escalation"),
                ("export_asset_escalation", "Can export Escalation"),
                ("delete_asset_escalation", "Can delete Escalation"),
        )
    def get_escalation_timedelta(self):
        """Returns the total time delta for escalation."""
        from datetime import timedelta
        total_minutes = (self.escalate_after_days * 24 * 60) + (self.escalate_after_hours * 60) + self.escalate_after_minutes
        return timedelta(minutes=total_minutes)

class AssetApproval(models.Model):
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
    asset_request   = models.ForeignKey(AssetRequest,related_name='approvals', on_delete=models.CASCADE)
    approver        = models.ForeignKey('UserManagement.CustomUser', on_delete=models.CASCADE)
    role            = models.CharField(max_length=50, null=True, blank=True)
    level           = models.IntegerField(default=1)
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES,default=PENDING)
    note            = models.TextField(null=True, blank=True)
    escalated       = models.BooleanField(default=False)
    escalated_at    = models.DateTimeField(null=True, blank=True)
    is_escalation   = models.BooleanField(default=False)
    created_at      = models.DateField(auto_now_add=True)
    created_by      = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')
    updated_at      = models.DateField(auto_now=True)

    def approve(self,note=None):
        self.status = self.APPROVED
        if note:
            self.note = note
        self.save()

        self.asset_request.move_to_next_level()
    def reject(self, note=None):
        self.status = self.REJECTED
        if note:
            self.note = note
        self.save()
        self.asset_request.status = 'Rejected'
        self.asset_request.save()
        send_notification_email(
            user=self.asset_request.created_by,
            employee=self.asset_request.employee,
            message=f"Your Request {self.asset_request.asset_type} has been Rejected!",
            template_type='asset_rejected',
            context={       
                **get_employee_context(self.asset_request.employee),
                'asset_type':self.asset_request.asset_type.name,
                'requested_asset':self.asset_request.requested_asset,
                'request_date ': self.asset_request.request_date,
                'reason' : self.asset_request.reason 
            },
            email_template_model=AssetEmailTemplate,
            notification_model=RequestNotification
        )

@receiver(post_save, sender=AssetRequest)
def create_initial_approval(sender, instance, created, **kwargs):

    if not created:
        return

    # ---------------- GET WORKFLOW ---------------- #
    workflow = AssetApprovalWorkflow.objects.filter(
        asset_type=instance.asset_type,
        branch__id__in=[instance.branch_id]   # ✅ FIXED
    ).first()

    # fallback workflow
    if not workflow:
        workflow = AssetApprovalWorkflow.objects.filter(
            asset_type=instance.asset_type,
            branch__isnull=True
        ).first()

    if not workflow:
        return

    # ---------------- GET FIRST LEVEL ---------------- #
    first_level = workflow.asset_levels.order_by('level').first()

    # FIX: DO NOT blindly create level (prevents broken flow)
    if not first_level:
        first_level = None

    approval_type = workflow.approval_type

    # ---------------- NO APPROVAL ---------------- #
    if approval_type == 'no_approval':

        approver = instance.created_by or instance.employee.users

        if not approver:
            raise Exception("No system user linked to employee.")

        AssetApproval.objects.create(
            asset_request=instance,
            approver=approver,
            role="Auto Approval",
            level=1,
            status=AssetApproval.APPROVED,
            created_by=approver
        )

        instance.status = 'approved'
        instance.save(update_fields=["status"])
        return

    # ---------------- REPORTING MANAGER ---------------- #
    if approval_type == 'reporting_manager':

        manager = getattr(instance.employee, 'emp_reporting_manager', None)

        if not manager:
            raise Exception("Employee has no reporting manager.")

        approval = AssetApproval.objects.create(
            asset_request=instance,
            approver=manager,
            role="Reporting Manager",
            level=first_level.level if first_level else 1,
            status=AssetApproval.PENDING,
            created_by=instance.created_by
        )

        if first_level:
            asset_schedule_escalation(approval, first_level)

        return

    # ---------------- MULTI APPROVAL ---------------- #
    if approval_type == 'multi_approval':

        if not first_level:
            raise Exception("Approval level not configured for workflow.")

        if first_level.approver:

            approval = AssetApproval.objects.create(
                asset_request=instance,
                approver=first_level.approver,
                role=first_level.role,
                level=first_level.level,
                status=AssetApproval.PENDING,
                created_by=instance.created_by
            )

            asset_schedule_escalation(approval, first_level)

        return



class AssetAllocation(models.Model):
    CONDITION_CHOICES = [
        ('healthy', 'Healthy'),
        ('minor_damage', 'Minor Damage'),
        ('major_damage', 'Major Damage'),
    ]

    asset            = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="allocations")
    employee         = models.ForeignKey(emp_master, on_delete=models.CASCADE, related_name="allocations")
    assigned_date    = models.DateField(null=True, blank=True)
    returned_date    = models.DateField(null=True, blank=True)
    return_condition = models.CharField(max_length=20, choices=CONDITION_CHOICES, null=True, blank=True)

    def __str__(self):
        return f"{self.asset} allocated to {self.employee}"
    def return_asset(self, condition, returned_date=None):
        if self.returned_date:
            raise ValueError("This asset has already been returned.")

        # Update the returned condition
        self.return_condition = condition

        # Assign the provided returned_date or use the current date
        self.returned_date = returned_date or timezone.now().date()
        self.save()

        # Update the asset status to available
        self.asset.status = "available"
        self.asset.condition = condition
        self.asset.save()
class AssetCustomField(models.Model):
    FIELD_TYPES = (
        ('dropdown', 'DropdownField'),
        ('radio', 'RadioButtonField'),
        ('date', 'DateField'),
        ('text', 'TextField'),
        ('checkbox', 'CheckboxField'),
    )
    asset_type       = models.ForeignKey(AssetType,on_delete=models.CASCADE,related_name='custom_fields',null=True)
    custom_field     = models.CharField(unique=True, max_length=100)  # Field name
    data_type        = models.CharField(max_length=20, choices=FIELD_TYPES, null=True, blank=True)
    dropdown_values  = models.JSONField(null=True, blank=True)
    radio_values     = models.JSONField(null=True, blank=True)
    checkbox_values  = models.JSONField(null=True, blank=True)

    def __str__(self):
        return f"{self.custom_field} ({self.asset_type})"

    def clean(self):
        # Validate field values based on type
        if self.data_type == 'dropdown' and not self.dropdown_values:
            raise ValidationError("Provide values for the dropdown options.")
        elif self.data_type == 'radio' and not self.radio_values:
            raise ValidationError("Provide values for the radio options.")
        elif self.data_type == 'checkbox' and not self.checkbox_values:
            raise ValidationError("Provide values for the checkbox options.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
    
class AssetCustomFieldValue(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='custom_field_values',null=True)
    custom_field = models.ForeignKey(AssetCustomField,on_delete=models.CASCADE,related_name='field_values',null=True)
    field_value = models.TextField(null=True, blank=True)  # Value provided by the user

    def __str__(self):
        return f"{self.asset.name} - {self.custom_field.name}: {self.field_value}"

    
    def clean(self):
        # Ensure the custom field belongs to the correct asset type
        if self.custom_field.asset_type != self.asset_master.asset_type:
            raise ValidationError("The custom field does not belong to this asset type.")

    def save(self, *args, **kwargs):
        # self.clean()
        super().save(*args, **kwargs)



class AssetReport(models.Model):
    file_name   = models.CharField(max_length=100,null=True,unique=True)
    report_data = models.FileField(upload_to='asset_report/', null=True, blank=True) 
    # created_at = models.DateTimeField(auto_now_add=True,null=True,blank =True)
    class Meta:
        permissions = (
            ('asset_export_report', 'Can export asset report'),
            # Add more custom permissions here
        )
    
    
    def __str__(self):
        return self.file_name
class AssetTransactionReport(models.Model):
    file_name   = models.CharField(max_length=100,null=True,unique=True)
    report_data = models.FileField(upload_to='asset_transaction_report/', null=True, blank=True) 
    # created_at = models.DateTimeField(auto_now_add=True,null=True,blank =True)
    class Meta:
        permissions = (
            ('asset_transaction_export_report', 'Can export asset transaction report'),
            # Add more custom permissions here
        )
    
    
    def __str__(self):
        return self.file_name

class GratuityTable(models.Model):
    minimum_value = models.DecimalField(max_digits=5, decimal_places=2, help_text="Minimum years of service")
    maximum_value = models.DecimalField(max_digits=5, decimal_places=2, help_text="Maximum years of service")
    resignation_days = models.PositiveIntegerField(help_text="Gratuity days for resignation")
    termination_days = models.PositiveIntegerField(help_text="Gratuity days for termination")
    is_active = models.BooleanField(default=True, help_text="Is this range active?")

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(minimum_value__lt=F('maximum_value')) | Q(maximum_value__isnull=True),
                name='valid_range'
            )
        ]

    def __str__(self):
        return f"{self.minimum_value} to {self.maximum_value} years - Resignation: {self.resignation_days}, Termination: {self.termination_days}"

class Folder(models.Model):
    name = models.CharField(max_length=255)
    parent = models.ForeignKey(
        'self', null=True, blank=True, related_name='subfolders', on_delete=models.CASCADE
    )
    created_by = models.ForeignKey("UserManagement.CustomUser", on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('name', 'parent')
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def path(self):
        """Return full folder path (like Company/Projects/Plans)."""
        parts = []
        folder = self
        while folder:
            parts.insert(0, folder.name)
            folder = folder.parent
        return "/".join(parts)


class Document(models.Model):
    folder = models.ForeignKey(Folder, related_name='documents', on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to='documents/')
    uploaded_by = models.ForeignKey("UserManagement.CustomUser", on_delete=models.SET_NULL, null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
class UserBranchAccess(models.Model):
    user = models.ForeignKey("UserManagement.CustomUser", on_delete=models.CASCADE, related_name='branch_accesses')
    # branch = models.ForeignKey("brnch_mstr", on_delete=models.CASCADE, related_name='user_accesses')
    branch = models.ManyToManyField('OrganisationManager.brnch_mstr',blank=True)

    def __str__(self):
        return f"{self.user}"
class BranchGeoFence(models.Model):
    branch = models.ManyToManyField(brnch_mstr, blank=True, related_name='geo_fences')
    location_type = models.CharField(
        max_length=20,
        choices=[
            ("office", "Office"),
            ("home", "Home"),
            ("remote", "Remote Site"),
            ("client", "Client Site"),
        ],null=True
    )
    location_name = models.CharField(max_length=200, help_text="e.g. Main Gate, Warehouse Entry")
    employee = models.ManyToManyField(
        emp_master,
        blank=True,
        related_name="allowed_geolocations"
    )    
    latitude = models.FloatField()
    longitude = models.FloatField()
    radius = models.IntegerField(default=50, help_text="Geofence radius in meters")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, related_name='%(class)s_created_by')

    def __str__(self):
        return f"{self.location_name} - {self.branch.branch_name}"