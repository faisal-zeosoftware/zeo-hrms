from django_tenants.signals import post_schema_sync
from django.dispatch import receiver
from django_tenants.utils import schema_context
from django.apps import apps
from datetime import date
from .models import AssetType,AssetApprovalWorkflow,AssetApprovalLevel,ctgry_master,dept_master,desgntn_master
@receiver(post_schema_sync)
def create_tenant_defaults(sender, tenant, **kwargs):
    with schema_context(tenant.schema_name):
        # Resolve models
        brnch_mstr = apps.get_model('OrganisationManager', 'brnch_mstr')
        leave_type = apps.get_model('calendars', 'leave_type')
        weekend_calendar = apps.get_model('calendars', 'weekend_calendar')
        assign_weekend = apps.get_model('calendars', 'assign_weekend')
        holiday_calendar = apps.get_model('calendars', 'holiday_calendar')
        holiday = apps.get_model('calendars', 'holiday')
        assign_holiday = apps.get_model('calendars', 'assign_holiday')
        SalaryComponent = apps.get_model('PayrollManagement', 'SalaryComponent')

        # Branch
        branch = brnch_mstr.objects.create(
            branch_name=tenant.name,
            # branch_logo=tenant.logo,
            branch_code=f"BR-{tenant.schema_name[:10].upper()}",
            probation_period_days=30,
            br_country=tenant.country,
            br_city="Sample City",
            br_pincode="123456",
            br_branch_nmbr_1=f"BN-{tenant.schema_name[:10].upper()}",
            br_branch_mail=getattr(tenant, 'company_email', 'branch@example.com'),
        )

        # Current year
        current_year = date.today().year

        # 1. Create Default Weekend Calendar (Saturdays and Sundays as off)
        weekend = weekend_calendar.objects.create(
            description=f"Default Weekend Calendar {current_year}",
            calendar_code=f"WEND-{current_year}-{tenant.schema_name[:5]}",
            year=current_year,
            monday='fullday',
            tuesday='fullday',
            wednesday='fullday',
            thursday='fullday',
            friday='fullday',
            saturday='leave',
            sunday='leave'
        )

        # Assign Weekend Calendar to Default Branch
        assignment_w = assign_weekend.objects.create(
            related_to='branch',
            weekend_model=weekend
        )
        assignment_w.branch.add(branch)

        # 2. Create Default Holiday Calendar
        h_calendar = holiday_calendar.objects.create(
            calendar_title=f"Default Holiday Calendar {current_year}",
            year=current_year
        )

        # Create Predefined Default Holidays for the Current Year
        default_holidays = [
            ("New Year's Day", date(current_year, 1, 1), date(current_year, 1, 1)),
            ("Labor Day", date(current_year, 5, 1), date(current_year, 5, 1)),
            ("Christmas Day", date(current_year, 12, 25), date(current_year, 12, 25)),
        ]

        for desc, start, end in default_holidays:
            holiday.objects.get_or_create(
                description=desc,
                defaults={
                    'calendar': h_calendar,
                    'start_date': start,
                    'end_date': end
                }
            )

        # Assign Holiday Calendar to Default Branch
        assignment_h = assign_holiday.objects.create(
            related_to='branch',
            holiday_model=h_calendar
        )
        assignment_h.branch.add(branch)

        # Default leave types
        default_leaves = [
            ("Sick Leave", "SL", "paid",True,False),
            ("Annual Leave", "AL", "paid",False,False),
            ("Casual Leave", "CL", "paid",False,False),
            ("Maternity Leave", "ML", "paid",False,False),
            ("Paternity Leave", "PL", "paid",False,False),
            ("Compensatory Leave", "COMP", "paid",False,True),
        ]
        for (name,code,leave_type_value,enable_leave_pay_rule,is_compensatory,) in default_leaves:
            leave_type.objects.update_or_create(
            code=f"{code}-{tenant.schema_name[:3].upper()}",
            branch=branch,
            defaults={
                "name": name,
                "type": leave_type_value,
                "unit": "days",
                "negative": False,
                "description": f"Default {name}",
                "allow_half_day": True,
                "include_weekend": False,
                "include_holiday": False,
                "use_common_workflow": True,
                "include_dashboard": True,
                "enable_leave_pay_rule":enable_leave_pay_rule,
                "is_compensatory":is_compensatory,
            },
        )

        # Default salary components - PASSING branch EXPLICITLY
        default_salary_components = [
            ("Basic", "addition", "BAS", True, "", False, True, False, False),
            ("HRA", "addition", "HRA", True, "", False, True, False, False),
            ("Air Ticket", "addition", "ATK", True, "", False, True, False, True),
            ("Petty Cash", "addition", "PC", False, "", False, True, False, False),
        ]
        for (
            name,
            component_type,
            code,
            is_fixed,
            formula,
            is_loan_component,
            show_in_payslip,
            is_advance_salary,
            is_air_ticket,
        ) in default_salary_components:
            SalaryComponent.objects.get_or_create(
                code=code,
                branch=branch, # Link to branch for uniqueness
                defaults={
                    "name": name,
                    "component_type": component_type,
                    "is_fixed": is_fixed,
                    "formula": formula,
                    "description": f"Default {name} Component",
                    "is_loan_component": is_loan_component,
                    "show_in_payslip": show_in_payslip,
                    "is_advance_salary": is_advance_salary,
                    "is_air_ticket": is_air_ticket,
                },
            )
        default_departments = [
            ("Human Resources", "HR"),
            ("Information Technology", "IT"),
            ("Finance", "FIN"),
            ("Sales", "SAL"),
            ("Operations", "OPS"),
        ]

        for name, code in default_departments:
            department, created = dept_master.objects.get_or_create(
                dept_code=f"{code}-{tenant.schema_name[:3].upper()}",
                defaults={
                    "dept_name": name,
                    "dept_description": f"Default {name} Department",
                }
            )

            department.branch.add(branch)

        # Default Designations
        default_designations = [
            ("Manager", "MGR"),
            ("Team Lead", "TL"),
            ("Executive", "EXE"),
            ("Senior Executive", "SE"),
            ("Assistant", "AST"),
        ]

        for title, code in default_designations:
            designation, created = desgntn_master.objects.get_or_create(
                desgntn_code=f"{code}-{tenant.schema_name[:3].upper()}",
                defaults={
                    "desgntn_job_title": title,
                    "desgntn_description": f"Default {title} Designation",
                }
            )

            designation.branch.add(branch)

        # Default Categories
        default_categories = [
            ("Permanent", "PERM"),
            ("Contract", "CONT"),
            ("Intern", "INT"),
            ("Trainee", "TRN"),
        ]

        for title, code in default_categories:
            category, created = ctgry_master.objects.get_or_create(
                ctgry_code=f"{code}-{tenant.schema_name[:3].upper()}",
                defaults={
                    "ctgry_title": title,
                    "ctgry_description": f"Default {title} Category",
                }
            )

            category.branch.add(branch)
from django.db.models.signals import post_save
# from django.dispatch import receiver
# from calendars .models import leave_type
from .models import brnch_mstr, DocumentNumbering
# from PayrollManagement .models import SalaryComponent
from datetime import timedelta
from django.utils import timezone
from django.db import transaction

@receiver(post_save, sender=brnch_mstr)
def create_defaults_for_branch(sender, instance, created, **kwargs):
    if not created:
        return

    with transaction.atomic():
        for doc_type, _ in DocumentNumbering.DOCUMENT_TYPES:

            raw_prefix = f"{instance.branch_code[:2]}-{doc_type[:3].upper()}"

        
            max_prefix_length = 8 
            prefix = raw_prefix[:max_prefix_length]

            DocumentNumbering.objects.get_or_create(
                branch_id=instance,
                type=doc_type,
                defaults={
                    'prefix': prefix,
                    'suffix': '',
                    'current_number': 0,
                    'total_length': 12,
                    'created_by': getattr(instance, 'br_created_by', None),
                    'start_date': timezone.now().date(),
                    'end_date': timezone.now().date() + timedelta(days=365),
                }
            )

@receiver(post_save, sender=AssetType)
def create_workflow_and_default_level(sender, instance, created, **kwargs):
    if not created:
        return

    # 1. Create Workflow
    workflow = AssetApprovalWorkflow.objects.create(
        asset_type=instance,
        approval_type='no_approval'
    )

    # 2. FIX: handle ManyToMany correctly
    if instance.branch.exists():
        workflow.branch.set(instance.branch.all())   # ✅ FIXED

    # 3. Create Default Level
    AssetApprovalLevel.objects.create(
        workflow=workflow,
        level=1,
        role="Auto Level",
        approver=None
    )