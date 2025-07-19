from django.db.models.signals import post_save
from django.dispatch import receiver
from calendars .models import leave_type
from .models import brnch_mstr, DocumentNumbering
from PayrollManagement .models import SalaryComponent
from datetime import timedelta
from django.utils import timezone

@receiver(post_save, sender=brnch_mstr)
def create_defaults_for_branch(sender, instance, created, **kwargs):
    if created:
        # Loop through all document types and create default entries
        for doc_type, _ in DocumentNumbering.DOCUMENT_TYPES:
            DocumentNumbering.objects.get_or_create(
                branch_id=instance,
                type=doc_type,
                defaults={
                    'prefix': f"{doc_type.upper().replace('_', '')}",
                    'suffix': '',
                    'current_number': 0,
                    'total_length': 12,
                    'created_by': instance.br_created_by,
                    'start_date':timezone.now().date(),
                    'end_date':timezone.now().date() + timedelta(days=365),
                }
            )
        default_leaves = [
            ('Sick Leave', 'SL', 'paid'),
            ('Annual Leave', 'AL', 'paid'),
            ('Casual Leave', 'CL', 'paid'),
            ('Maternity Leave', 'ML', 'paid'),
            ('Paternity Leave', 'PL', 'paid'),
        ]

        for name, code, leave_type_value in default_leaves:
            leave_type.objects.get_or_create(
                name=name,
                code=code,
                branch=instance,
                defaults={
                    'type': leave_type_value,
                    'unit': 'days',
                    'negative': False,
                    'description': f'Default {name}',
                    'allow_half_day': True,
                    'include_weekend_and_holiday': False,
                    'use_common_workflow': True,
                    'include_dashboard': True,
                    'created_by': instance.br_created_by,
                }
            )
        default_salary_components = [
            ("Basic", "addition", "BAS", True, "", False, True, False, False),
            ("HRA", "addition", "HRA", True, "", False, True, False, False),
            ("Air Ticket", "addition", "ATK", True, "", False, True, False, True),
            ("Petty Cash", "addition", "PC", False, "", False, True, False, False),
        ]

        for name, component_type, code, is_fixed, formula, is_loan_component, show_in_payslip, is_advance_salary, is_air_ticket in default_salary_components:
            SalaryComponent.objects.get_or_create(
                name=name,
                branch=instance,
                defaults={
                    'component_type': component_type,
                    'code': code,
                    'is_fixed': is_fixed,
                    'formula': formula,
                    'description': f'Default {name} Component',
                    'is_loan_component': is_loan_component,
                    'show_in_payslip': show_in_payslip,
                    'is_advance_salary': is_advance_salary,
                    'is_air_ticket': is_air_ticket,
                }
            )