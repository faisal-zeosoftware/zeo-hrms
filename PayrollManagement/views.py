from django.shortcuts import render
from .models import (SalaryComponent,EmployeeSalaryStructure,PayslipComponent,Payslip,PayrollRun,LoanType,LoanApplication,
                     LoanRepayment,LoanApprovalLevels,LoanApproval,PayslipApproval,PayslipCommonWorkflow,AdvanceSalaryRequest,AdvanceSalaryApproval,AdvanceCommonWorkflow,AirTicketPolicy,AirTicketAllocation,AirTicketRequest,
                     LoanEmailTemplate,LoanNotification,AdvanceSalaryEmailTemplate,AdvanceSalaryNotification,AirTicketRule,AirticketApproval,AirticketEmailTemplate,AirticketWorkflow,PayStructure,PayslipLeave,AirticketApprovalWorkflow,AdvanceApprovalWorkflow)

from .serializer import (SalaryComponentSerializer,EmpBulkuploadSalaryStructureSerializer,EmployeeSalaryStructureSerializer,PayslipSerializer,PaySlipComponentSerializer,LoanTypeSerializer,LoanApplicationSerializer,LoanRepaymentSerializer,
                         LoanApprovalSerializer,LoanApprovalLevelsSerializer,PayrollRunSerializer,PayslipConfirmedSerializer,SIFSerializer,AdvanceSalaryRequestSerializer,AdvanceSalaryApprovalSerializer,AdvanceCommonWorkflowSerializer,PayslipCommonWorkflowSerializer,PayslipApprovalSerializer,AirTicketPolicySerializer,AirTicketAllocationSerializer
                         ,AirTicketRequestSerializer,LoanEmailTemplateSerializer,LoanNotificationSerializer,AdvSalaryEmailTemplateSerializer,AdvSalaryNotificationSerializer,AirTicketRuleSerializer,AirticketEmailTemplateSerializer,AirticketEscalationRuleSerializer,AirticketWorkflowSerializer,AirtcketApprovalSerializer,LoanEscalationRuleSerializer,
                         AdvSalaryEscalationRuleSerializer,PayStructureSerializer,PayslipLeaveSerializer,AirticketApprovalWorkflowSerializer,AdvanceApprovalWorkflowSerializer
                         )

from rest_framework import status,generics,viewsets,permissions
from .permissions import(SalaryComponentPermission,EmployeeSalaryStructurePermission,PayrollRunPermission,PayslipComponentPermission,PayslipPermission)
from .resource import EmployeeSalaryStructureResource
from EmpManagement.models import emp_master
from rest_framework.decorators import action
from OrganisationManager.models import DocumentNumbering
from django.core.exceptions import ValidationError
from rest_framework.response import Response
from django.utils import timezone
from rest_framework.exceptions import NotFound
from tablib import Dataset
from rest_framework.parsers import MultiPartParser, FormParser
from django.db import transaction
from .utils import generate_payslip_pdf,send_payslip_email
from .models import (SalaryComponent,EmployeeSalaryStructure,PayrollRun,Payslip,PayslipComponent,LoanType,LoanApplication,
                     LoanRepayment,LoanApprovalLevels,LoanApproval)
from .serializer import (SalaryComponentSerializer,EmployeeSalaryStructureSerializer,PayslipSerializer,PaySlipComponentSerializer,LoanTypeSerializer,LoanApplicationSerializer,LoanRepaymentSerializer,
                         LoanApprovalSerializer,LoanApprovalLevelsSerializer,PayrollRunSerializer)
from rest_framework import status,generics,viewsets,permissions
from datetime import datetime
import logging
from django_tenants.utils import get_tenant_model
from django.http import HttpResponse
from rest_framework.views import APIView
import csv
from rest_framework import serializers
import pytz
from .tasks import send_payslip_email_task,accrue_air_tickets
from django.db import connection
from django.db.models import Q
from openpyxl import Workbook
from tablib import Dataset
from openpyxl.styles import PatternFill,Alignment,Font,NamedStyle,Border, Side
from django.http import HttpResponse
import io



# Set up logging
logger = logging.getLogger(__name__)
# Create your views here.


class SalaryComponentViewSet(viewsets.ModelViewSet):
    queryset = SalaryComponent.objects.all()
    serializer_class = SalaryComponentSerializer


class EmployeeSalaryStructureViewSet(viewsets.ModelViewSet):
    queryset = EmployeeSalaryStructure.objects.all()
    serializer_class = EmployeeSalaryStructureSerializer

class PayslipViewSet(viewsets.ModelViewSet):
    queryset = Payslip.objects.all()
    serializer_class = PayslipSerializer
    @action(detail=False, methods=['get'])
    def aproved_payslips(self, request):
        aproved_payslips = self.queryset.filter(status='Approved')
        serializer = self.get_serializer(aproved_payslips, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    @action(detail=False, methods=['get'], url_path='employee/(?P<emp_code>[^/.]+)/download/(?P<year>\d{4})/(?P<month>\d{1,2})')
    def download_employee_payslip_by_month(self, request, emp_code=None, year=None, month=None):
        """Download a payslip for a specific employee for a given month and year."""
        try:
            # Ensure month and year are integers
            month = int(month)
            year = int(year)
            if not 1 <= month <= 12:
                return Response({"error": "Month must be between 1 and 12"}, status=status.HTTP_400_BAD_REQUEST)

            # Fetch the employee by emp_code
            try:
                employee = emp_master.objects.get(emp_code=emp_code)
            except emp_master.DoesNotExist:
                return Response(
                    {"error": f"No employee found with emp_code {emp_code}"}, 
                    status=status.HTTP_404_NOT_FOUND
                )

            # Fetch the payslip for the employee, month, and year
            payslip = Payslip.objects.get(
                employee=employee,
                payroll_run__month=month,
                payroll_run__year=year
            )
            return generate_payslip_pdf(request, payslip)
        except Payslip.DoesNotExist:
            return Response(
                {"error": f"No payslip found for employee {emp_code} for {month}/{year}"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        except ValueError:
            return Response({"error": "Invalid year or month format"}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'], url_path='employee/(?P<employee_id>\d+)/filter/(?P<year>\d{4})/(?P<month>\d{1,2})')
    def filter_employee_payslip_by_month(self, request, employee_id=None, year=None, month=None):
        """Retrieve payslip data for a specific employee for a given month and year."""
        try:
            # Ensure month and year are integers
            month = int(month)
            year = int(year)
            if not 1 <= month <= 12:
                return Response({"error": "Month must be between 1 and 12"}, status=status.HTTP_400_BAD_REQUEST)

            # Fetch the payslip for the employee, month, and year
            payslip = Payslip.objects.get(
                employee_id=employee_id,
                payroll_run__month=month,
                payroll_run__year=year
            )
            serializer = self.get_serializer(payslip)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Payslip.DoesNotExist:
            return Response(
                {"error": f"No payslip found for employee {employee_id} for {month}/{year}"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        except ValueError:
            return Response({"error": "Invalid year or month format"}, status=status.HTTP_400_BAD_REQUEST)
    @action(detail=True, methods=['post'], url_path='upload-pdf')
    def upload_pdf(self, request, pk=None):
        payslip = self.get_object()
        pdf_file = request.FILES.get('payslip_pdf')
        send_email = request.data.get('send_email', False)

        # Convert string to boolean
        if isinstance(send_email, str):
            send_email = send_email.lower() in ['true', '1', 'yes']

        if not pdf_file:
            return Response({'error': 'PDF file is required.'}, status=status.HTTP_400_BAD_REQUEST)

        # Save PDF and flag
        payslip.payslip_pdf.save(pdf_file.name, pdf_file, save=True)
        payslip.send_email = send_email
        payslip.save(update_fields=['send_email'])

        # Trigger celery task immediately if requested
        if send_email:
            schema_name = connection.schema_name
            send_payslip_email_task.delay(payslip.id, schema_name)

        message = "PDF uploaded."
        if send_email:
            message += " Email will be sent shortly (triggered immediately)."

        return Response({'message': message}, status=status.HTTP_200_OK)

class PayslipComponentViewSet(viewsets.ModelViewSet):
    queryset = PayslipComponent.objects.all()
    serializer_class = PaySlipComponentSerializer


class PayrollRunViewSet(viewsets.ModelViewSet):
    queryset = PayrollRun.objects.all()
    serializer_class = PayrollRunSerializer

class EmpBulkuploadSalaryStructureViewSet(viewsets.ModelViewSet):
    queryset = EmployeeSalaryStructure.objects.all()
    serializer_class = EmpBulkuploadSalaryStructureSerializer
    @action(detail=False, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def bulk_upload(self, request):
        if request.method == 'POST' and request.FILES.get('file'):
            excel_file = request.FILES['file']
            dataset = Dataset()
            file_name = excel_file.name.lower()

            try:
                if file_name.endswith('.xlsx'):
                    dataset.load(excel_file.read(), format='xlsx')

                elif file_name.endswith('.csv'):
                    dataset.load(
                        excel_file.read().decode('utf-8'),
                        format='csv'
                    )

                else:
                    return Response(
                        {"error": "Invalid file format. Upload .xlsx or .csv only."},
                        status=400
                    )

                resource = EmployeeSalaryStructureResource()
                all_errors = []
                valid_rows = []

                with transaction.atomic():
                    for row_idx, row in enumerate(dataset.dict, start=2):
                        try:
                            resource.before_import_row(row, row_idx=row_idx)
                        except ValidationError as e:
                            all_errors.extend(
                                [f"Row {row_idx}: {error}" for error in e.messages]
                            )

                if all_errors:
                    return Response({"errors": all_errors}, status=400)

                with transaction.atomic():
                    result = resource.import_data(
                        dataset,
                        dry_run=False,
                        raise_errors=True
                    )

                return Response(
                    {"message": f"{result.total_rows} records created successfully"}
                )

            except Exception as e:
                return Response({"error": str(e)}, status=400)

        return Response({"error": "Please provide an Excel or CSV file."}, status=400)
    
    @action(detail=False, methods=['get'])
    def download_default_excel_file(self, request):
        resource = EmployeeSalaryStructureResource()
        headers = [field.column_name for field in resource.fields.values()]
        wb = Workbook()

        # ======== Common Styles ========
        black_font = Font(color="000000", bold=True)
        blue_fill = PatternFill(start_color="1E90FF", end_color="1E90FF", fill_type="solid")
        yellow_fill = PatternFill(start_color="FFF8DC", end_color="FFF8DC", fill_type="solid")  # light cream/yellow
        border_style = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )

        # Helper function to style header row
        def style_header_row(ws, max_cols=10):
            """Style header row with blue fill and black bold text across full width."""
            for col in range(1, max_cols + 1):
                cell = ws.cell(row=1, column=col)
                if not cell.value:
                    cell.value = ""
                cell.fill = blue_fill
                cell.font = black_font
                cell.border = border_style
                ws.column_dimensions[cell.column_letter].width = 25
            ws.freeze_panes = "A2"  # freeze header
        # ======================================================
        # Sheet 1: SalaryComponent
        # ======================================================
        ws1 = wb.active
        ws1.title = "Salary Componenet"
        for col_num, header in enumerate(headers, 1):
            ws1.cell(row=1, column=col_num, value=header)

        style_header_row(ws1, max_cols=len(headers))
         # ======================================================
        # Save response
        # ======================================================
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        response = HttpResponse(
            output,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = (
            'attachment; filename="SalaryComponent_BulkUpload_Template.xlsx"'
        )
        return response
    
    @action(detail=False, methods=['get'])
    def download_default_csv_file(self, request):
        resource = EmployeeSalaryStructureResource()
        headers = [field.column_name for field in resource.fields.values()]
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)  # only headers, no data

        response = HttpResponse(output.getvalue(), content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="Employee_SalaryComponent_Template.csv"'
        return response

class PayslipConfirmedViewSet(viewsets.ModelViewSet):
    queryset = Payslip.objects.all()
    serializer_class = PayslipConfirmedSerializer
    def get_queryset(self):
        return Payslip.objects.filter(status='processed')

class LoanTypeviewset(viewsets.ModelViewSet):
    queryset = LoanType.objects.all()
    serializer_class = LoanTypeSerializer

class LoanApplicationviewset(viewsets.ModelViewSet):
    queryset = LoanApplication.objects.all()
    serializer_class = LoanApplicationSerializer

    def perform_create(self, serializer):
        with transaction.atomic():
            employee = serializer.validated_data.get('employee')
            document_number = serializer.validated_data.get('document_number')

            if not employee:
                raise ValidationError("Employee is required.")
            branch_id = employee.emp_branch_id or employee.work_location

            if not branch_id:
                raise ValidationError("Employee branch is missing in employee master.")

            try:
                doc_config = DocumentNumbering.objects.get(
                    branch_id=branch_id,
                    type='loan_request'
                )
            except DocumentNumbering.DoesNotExist:
                raise NotFound(
                    f"No document numbering configuration found for branch {branch_id} and loan request."
                )

            current_date = timezone.now().date()
            if document_number:
                if doc_config.start_date and doc_config.end_date:
                    if not (doc_config.start_date <= current_date <= doc_config.end_date):
                        raise ValidationError(
                            "Document number cannot be assigned outside the valid date range."
                        )
                if LoanApplication.objects.filter(document_number=document_number).exists():
                    raise ValidationError("Document number already exists.")

            else:
                document_number = doc_config.get_next_number()

            serializer.save(document_number=document_number)


    @action(detail=False, methods=['get'], url_path='paused-loans')
    def paused_loans(self, request):
        paused_loans = self.queryset.filter(status='Paused')
        serializer = self.get_serializer(paused_loans, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    @action(detail=True, methods=['post'])
    def pause(self, request, pk=None):
        """Pause loan repayments with a reason."""
        loan = self.get_object()
        pause_date = request.data.get('pause_start_date')
        reason = request.data.get('pause_reason')

        if not pause_date:
            return Response({"error": "Pause start date is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            loan.pause(start_date=pause_date, reason=reason)
            return Response({"status": "paused", "pause_date": pause_date, "reason": reason}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def resume(self, request, pk=None):
        """Resume loan repayments with a reason."""
        loan = self.get_object()
        resume_date = request.data.get('resume_date')
        reason = request.data.get('resume_reason')

        if not resume_date:
            return Response({"error": "Resume date is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            loan.resume(resume_date=resume_date, reason=reason)
            return Response({"status": "resumed", "resume_date": resume_date, "reason": reason}, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
class PayslipConfirmedViewSet(viewsets.ModelViewSet):
    queryset = Payslip.objects.all()
    serializer_class = PayslipConfirmedSerializer
    def get_queryset(self):
        return Payslip.objects.filter(status='processed')
class LoanRepaymentviewset(viewsets.ModelViewSet):
    queryset = LoanRepayment.objects.all()
    serializer_class = LoanRepaymentSerializer

class LoanApprovalLevelsviewset(viewsets.ModelViewSet):
    queryset = LoanApprovalLevels.objects.all()
    serializer_class = LoanApprovalLevelsSerializer

class LoanApprovalviewset(viewsets.ModelViewSet):
    queryset = LoanApproval.objects.all()
    serializer_class = LoanApprovalSerializer
    lookup_field = 'pk'
    def get_queryset(self):
        """
        Filter approvals based on the authenticated user.
        """
        user = self.request.user  # Get the logged-in user
        if user.is_superuser:
            return LoanApproval.objects.all()
        return LoanApproval.objects.filter(approver=user)  # Filter approvals assigned to the user
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        approvals = self.get_object()
        note = request.data.get('note')  # Get the note from the request
        approvals.approve(note=note)
        return Response({'status': 'approved', 'note': note}, status=status.HTTP_200_OK)

    
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        approval = self.get_object()
        note = request.data.get('note')
        rejection_reason_id = request.data.get('rejection_reason')

        if not rejection_reason_id:
            raise ValidationError("Rejection reason is required.")

        # try:
        #     rejection_reason = LvRejectionReason.objects.get(id=rejection_reason_id)
        # except LvRejectionReason.DoesNotExist:
        #     raise ValidationError("Invalid rejection reason.")

        approval.reject(rejection_reason=rejection_reason_id, note=note)
        return Response({'status': 'rejected', 'note': note, 'rejection_reason': rejection_reason_id}, status=status.HTTP_200_OK)
class SIFDataView(APIView):
    def post(self, request):
        serializer = SIFSerializer(data=request.data)
        if serializer.is_valid():
            try:
                # sif_data, total_salary = serializer.generate_sif_data()
                sif_data, total_salary, skipped_employees = serializer.generate_sif_data()
            except serializers.ValidationError as e:
                return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

            # Log total_salary for debugging
            logger.debug(f"total_salary type: {type(total_salary)}, value: {total_salary}")

            # Get tenant for HDR and SCR
            tenant = get_tenant_model().objects.filter(schema_name=request.tenant.schema_name).first()

            # HDR row
            hdr_row = {
                'Type': 'HDR',
                'Person ID': tenant.name if tenant and tenant.name else 'UNKNOWN_COMPANY',
                'Routing Code': '',
                'IBAN Number': '',
                'Pay Start Date': '',
                'Pay End Date': '',
                'Number of Days': '',
                'Fixed Income': '',
                'Variable Income': '',
                'Days on Leave': ''
            }

            # SCR row
            payroll_run = PayrollRun.objects.get(id=serializer.validated_data['payroll_run_id'])
            month, year = payroll_run.month, payroll_run.year

            # Get tenant's timezone dynamically
            tenant_timezone = 'UTC'
            if tenant and tenant.country and tenant.country.timezone:
                tenant_timezone = tenant.country.timezone
            try:
                tz = pytz.timezone(tenant_timezone)
            except pytz.UnknownTimeZoneError:
                tz = pytz.UTC
                logger.warning(f"Invalid timezone '{tenant_timezone}' for tenant {tenant.schema_name}. Falling back to UTC.")

            current_time = datetime.now(tz=tz)

            employer_unique_id = tenant.employer_unique_id.zfill(13) if tenant and tenant.employer_unique_id else '0' * 13
            bank_routing_code = tenant.bank_routing_code if tenant and tenant.bank_routing_code else '0' * 9

            # Handle total_salary
            salary_value = total_salary
            if isinstance(total_salary, dict):
                # Adjust 'amount' to the correct key based on your dictionary structure
                salary_value = total_salary.get('amount', 0.0)
                logger.debug(f"Extracted salary_value: {salary_value}")
            else:
                try:
                    salary_value = float(total_salary)
                except (TypeError, ValueError):
                    logger.error(f"Invalid total_salary value: {total_salary}")
                    return Response({"error": "Invalid total_salary value"}, status=status.HTTP_400_BAD_REQUEST)

            scr_row = {
                'Type': 'SCR',
                'Person ID': employer_unique_id,
                'Routing Code': bank_routing_code,
                'IBAN Number': current_time.strftime('%Y-%m-%d'),
                'Pay Start Date': current_time.strftime('%H%M'),
                'Pay End Date': f"{month:02d}{year}",
                'Number of Days': len(sif_data),
                'Fixed Income': f"{salary_value:.2f}",  # Use the numeric value
                'Variable Income': 'AED',
                'Days on Leave': ''
            }

            # Combine all rows
            response_data = {
                'status': 'success',
                'data': {
                    'hdr': hdr_row,
                    'edr': sif_data,
                    'scr': scr_row,
                    "skipped_employees": skipped_employees,  # 👈 new field
                }
            }

            return Response(response_data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class PayslipCommonWorkflowViewSet(viewsets.ModelViewSet):
    queryset = PayslipCommonWorkflow.objects.all()
    serializer_class = PayslipCommonWorkflowSerializer

class PayslipApprovalViewSet(viewsets.ModelViewSet):
    queryset = PayslipApproval.objects.all()
    serializer_class = PayslipApprovalSerializer
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        approval = self.get_object()
        note = request.data.get('note')
        if approval.status != 'Pending':
            raise ValidationError("This approval has already been processed.")
        approval.approve(note=note)
        return Response({'status': 'approved'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        approval = self.get_object()
        note = request.data.get('note')
        rejection_reason = request.data.get('rejection_reason')

        if not rejection_reason:
            raise ValidationError("Rejection reason is required.")

        if approval.status != 'Pending':
            raise ValidationError("This approval has already been processed.")

        approval.reject(rejection_reason=rejection_reason, note=note)
        return Response({'status': 'rejected'}, status=status.HTTP_200_OK)
    @action(detail=False, methods=['post'])
    def bulk_approve(self, request):
        ids = request.data.get('approval_ids', [])
        note = request.data.get('note', '')

        if not ids:
            raise ValidationError("approval_ids list is required.")

        approvals = PayslipApproval.objects.filter(id__in=ids, status='Pending')
        for approval in approvals:
            approval.approve(note=note)

        return Response({'status': f"{approvals.count()} requests approved."}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'])
    def bulk_reject(self, request):
        ids = request.data.get('approval_ids', [])
        note = request.data.get('note', '')
        rejection_reason = request.data.get('rejection_reason', '')

        if not ids:
            raise ValidationError("approval_ids list is required.")
        if not rejection_reason:
            raise ValidationError("rejection_reason is required.")

        approvals = PayslipApproval.objects.filter(id__in=ids, status='Pending')
        for approval in approvals:
            approval.reject(rejection_reason=rejection_reason, note=note)

        return Response({'status': f"{approvals.count()} requests rejected."}, status=status.HTTP_200_OK)

class AdvanceSalaryRequestViewset(viewsets.ModelViewSet):
    queryset = AdvanceSalaryRequest.objects.all()
    serializer_class = AdvanceSalaryRequestSerializer
    def perform_create(self, serializer):
        with transaction.atomic():
            employee = serializer.validated_data.get('employee')
            document_number = serializer.validated_data.get('document_number')  # Get manually entered document number

            branch_id = employee.emp_branch_id.id  

            try:
                doc_config = DocumentNumbering.objects.get(
                    branch_id=branch_id,
                    type='advance_salary_request',
                    # leave_type__isnull=True
                )
            except DocumentNumbering.DoesNotExist:
                raise NotFound(f"No document numbering configuration found for branch {branch_id} and Advance Salary request.")

            current_date = timezone.now().date()

            # Validate if the manually entered document number is within the date range
            if document_number:
                if doc_config.start_date and doc_config.end_date:
                    if not (doc_config.start_date <= current_date <= doc_config.end_date):
                        raise ValidationError("Document number cannot be assigned outside the valid date range.")
            else:
                # If no document number is entered, generate one automatically
                document_number = doc_config.get_next_number()

            serializer.save(document_number=document_number)
    @action(detail=True, methods=['post'])
    def pause(self, request, pk=None):
        """Pause advance salary request with a reason."""
        loan = self.get_object()
        pause_date = request.data.get('pause_start_date')
        reason = request.data.get('pause_reason')

        if not pause_date:
            return Response({"error": "Pause start date is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            loan.pause(start_date=pause_date, reason=reason)
            return Response({"status": "paused", "pause_date": pause_date, "reason": reason}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def resume(self, request, pk=None):
        """Resume loan repayments with a reason."""
        loan = self.get_object()
        resume_date = request.data.get('resume_date')
        reason = request.data.get('resume_reason')

        if not resume_date:
            return Response({"error": "Resume date is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            loan.resume(resume_date=resume_date, reason=reason)
            return Response({"status": "resumed", "resume_date": resume_date, "reason": reason}, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

class AdvanceCommonWorkflowViewSet(viewsets.ModelViewSet):
    queryset = AdvanceApprovalWorkflow.objects.all()
    serializer_class = AdvanceApprovalWorkflowSerializer

class AdvanceSalaryApprovalViewSet(viewsets.ModelViewSet):
    queryset = AdvanceSalaryApproval.objects.all()
    serializer_class = AdvanceSalaryApprovalSerializer
    def get_queryset(self):
        """
        Filter approvals based on the authenticated user.
        """
        user = self.request.user  # Get the logged-in user
        if user.is_superuser:
            return AdvanceSalaryApproval.objects.all()
        return AdvanceSalaryApproval.objects.filter(approver=user)  # Filter approvals assigned to the user

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        approval = self.get_object()
        note = request.data.get('note')
        if approval.status != 'Pending':
            raise ValidationError("This approval has already been processed.")
        approval.approve(note=note)
        return Response({'status': 'approved'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        approval = self.get_object()
        note = request.data.get('note')
        rejection_reason = request.data.get('rejection_reason')

        if not rejection_reason:
            raise ValidationError("Rejection reason is required.")

        # if approval.status != 'Pending':
        #     raise ValidationError("This approval has already been processed.")

        approval.reject(rejection_reason=rejection_reason, note=note)
        return Response({'status': 'rejected'}, status=status.HTTP_200_OK)
class AirTicketRuleViewSet(viewsets.ModelViewSet):
    queryset = AirTicketRule.objects.all()
    serializer_class = AirTicketRuleSerializer
    
class AirTicketPolicyViewSet(viewsets.ModelViewSet):
    queryset = AirTicketPolicy.objects.all()
    serializer_class = AirTicketPolicySerializer
    # permission_classes = [IsAuthenticated]

class AirTicketAllocationViewSet(viewsets.ModelViewSet):
    queryset = AirTicketAllocation.objects.all()
    serializer_class = AirTicketAllocationSerializer
    # permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'])
    def trigger_auto_allocation(self, request):
        # Trigger automatic allocation task
        try:
            accrue_air_tickets.delay()
            logger.info("Automatic air ticket allocation task triggered")
            return Response({"message": "Automatic allocation task triggered"}, status=status.HTTP_202_ACCEPTED)
        except Exception as e:
            logger.error(f"Error triggering automatic allocation: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
class AirTicketRequestViewSet(viewsets.ModelViewSet):
    queryset = AirTicketRequest.objects.all()
    serializer_class = AirTicketRequestSerializer
    # permission_classes = [IsAuthenticated]

    # def get_queryset(self):
    #     return AirTicketRequest.objects.filter(employee__users=self.request.user)


    def perform_create(self, serializer):
        with transaction.atomic():
            employee = serializer.validated_data.get('employee')
            document_number = serializer.validated_data.get('document_number')  # Get manually entered document number

            branch_id = employee.emp_branch_id.id  

            try:
                doc_config = DocumentNumbering.objects.get(
                    branch_id=branch_id,
                    type='air_ticket_request',
                    
                )
            except DocumentNumbering.DoesNotExist:
                raise NotFound(f"No document numbering configuration found for branch {branch_id} and Air Ticket request.")

            current_date = timezone.now().date()

            # Validate if the manually entered document number is within the date range
            if document_number:
                if doc_config.start_date and doc_config.end_date:
                    if not (doc_config.start_date <= current_date <= doc_config.end_date):
                        raise ValidationError("Document number cannot be assigned outside the valid date range.")
            else:
                # If no document number is entered, generate one automatically
                document_number = doc_config.get_next_number()

            serializer.save(document_number=document_number)
class AirticketWorkflowViewSet(viewsets.ModelViewSet):
    queryset = AirticketApprovalWorkflow.objects.all()
    serializer_class = AirticketApprovalWorkflowSerializer

class AirticketApprovalViewSet(viewsets.ModelViewSet):
    queryset = AirticketApproval.objects.all()
    serializer_class = AirtcketApprovalSerializer
    # def get_queryset(self):
    #     """
    #     Filter approvals based on the authenticated user.
    #     """
    #     user = self.request.user  # Get the logged-in user
    #     if user.is_superuser:
    #         return AirticketApproval.objects.all()
    #     return AirticketApproval.objects.filter(approver=user)  # Filter approvals assigned to the user

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        approval = self.get_object()
        note = request.data.get('note')
        if approval.status != 'Pending':
            raise ValidationError("This approval has already been processed.")
        approval.approve(note=note)
        return Response({'status': 'approved'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        approval = self.get_object()
        note = request.data.get('note')
        rejection_reason = request.data.get('rejection_reason')

        if not rejection_reason:
            raise ValidationError("Rejection reason is required.")

        # if approval.status != 'Pending':
        #     raise ValidationError("This approval has already been processed.")

        approval.reject(rejection_reason=rejection_reason, note=note)
        return Response({'status': 'rejected'}, status=status.HTTP_200_OK)  
class AirticketEmailTemplateViewSet(viewsets.ModelViewSet):
    queryset = AirticketEmailTemplate.objects.all()
    serializer_class = AirticketEmailTemplateSerializer
    @action(detail=False, methods=['get'], url_path='placeholders')
    def placeholder_list(self, request):
        placeholders = {
            'employee': [
                '{{ document_number }}',
                '{{ recipient_name }}',
                '{{ emp_first_name }}',
                '{{ emp_last_name }}',
                '{{ emp_gender }}',
                '{{ emp_date_of_birth }}',
                '{{ emp_personal_email }}',
                '{{ emp_company_email }}',
                '{{ emp_branch_name }}',
                '{{ emp_department_name }}',
                '{{ emp_designation_name }}',
                '{{ requested_amount }}',
                '{{ reason }}',
                '{{ remarks }}',
                '{{ rejection_reason }}',

            ]
        }
        return Response(placeholders)
class AirticketEscalationRuleViewSet(viewsets.ModelViewSet):
    """
    API for managing escalation settings on each approval level.
    """
    serializer_class = AirticketEscalationRuleSerializer
    queryset = AirticketWorkflow.objects.all().order_by('level')

    def update(self, request, *args, **kwargs):
        """
        Update only escalation fields for a level.
        """
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response({
            "message": "Escalation rule updated successfully",
            "data": serializer.data
        }, status=status.HTTP_200_OK)
    @action(detail=True, methods=['post'])
    def reset(self, request, pk=None):
        instance = self.get_object()
        instance.escalate_to = None
        instance.escalate_after_days = 0
        instance.escalate_after_hours = 0
        instance.escalate_after_minutes = 0
        instance.save()
        return Response({"message": "Escalation rule reset successfully"}, status=200)
    
class LoanEmailTemplateViewSet(viewsets.ModelViewSet):
    queryset = LoanEmailTemplate.objects.all()
    serializer_class = LoanEmailTemplateSerializer
    @action(detail=False, methods=['get'], url_path='placeholders')
    def placeholder_list(self, request):
        placeholders = {
            'employee': [
                '{{ document_number }}',
                '{{ loan_type }}',
                '{{ reason }}',
                '{{ recipient_name }}',
                '{{ emp_first_name }}',
                '{{ emp_last_name }}',
                '{{ emp_gender }}',
                '{{ emp_date_of_birth }}',
                '{{ emp_personal_email }}',
                '{{ emp_company_email }}',
                '{{ emp_branch_name }}',
                '{{ emp_department_name }}',
                '{{ emp_designation_name }}',
                '{{ amount_requested }}',
                '{{ repayment_period }}',
                '{{ emi_amount }}',
                '{{ remaining_balance }}',
                '{{ status }}',
                '{{ rejection_reason }}',
            ]
        }
        return Response(placeholders)

class LoanNotificationViewSet(viewsets.ModelViewSet):
    queryset = LoanNotification.objects.all()
    serializer_class = LoanNotificationSerializer
    def get_queryset(self):
        user = self.request.user

        # Admin / staff / superuser → see all request notifications
        if user.is_superuser or user.is_staff:
            return LoanNotification.objects.all().order_by('-created_at')

        # Normal user → show request notifications assigned directly to them
        qs = LoanNotification.objects.filter(
            Q(recipient_user=user) |
            Q(recipient_employee__users=user)      # employee assigned to this user
        ).order_by('-created_at')

        return qs

class AdvSalaryEmailTemplateViewSet(viewsets.ModelViewSet):
    queryset = AdvanceSalaryEmailTemplate.objects.all()
    serializer_class = AdvSalaryEmailTemplateSerializer
    @action(detail=False, methods=['get'], url_path='placeholders')
    def placeholder_list(self, request):
        placeholders = {
            'employee': [
                '{{ document_number }}',
                '{{ reason }}',
                '{{ recipient_name }}',
                '{{ emp_first_name }}',
                '{{ emp_last_name }}',
                '{{ emp_gender }}',
                '{{ emp_date_of_birth }}',
                '{{ emp_personal_email }}',
                '{{ emp_company_email }}',
                '{{ emp_branch_name }}',
                '{{ emp_department_name }}',
                '{{ emp_designation_name }}',
                '{{ requested_amount }}',
                '{{ reason }}',
                '{{ remarks }}',
                '{{ rejection_reason }}'
            ]
        }
        return Response(placeholders)
class AdvSalaryNotificationViewSet(viewsets.ModelViewSet):
    queryset = AdvanceSalaryNotification.objects.all()
    serializer_class = AdvSalaryNotificationSerializer
    def get_queryset(self):
        user = self.request.user

        # Admin / staff / superuser → see all request notifications
        if user.is_superuser or user.is_staff:
            return AdvanceSalaryNotification.objects.all().order_by('-created_at')

        # Normal user → show request notifications assigned directly to them
        qs = AdvanceSalaryNotification.objects.filter(
            Q(recipient_user=user) |
            Q(recipient_employee__users=user)      # employee assigned to this user
        ).order_by('-created_at')

        return qs

class AdvSalaryEscalationRuleViewSet(viewsets.ModelViewSet):
    """
    API for managing escalation settings on each approval level.
    """
    serializer_class = AdvSalaryEscalationRuleSerializer
    queryset = AdvanceCommonWorkflow.objects.all().order_by('level')

    def update(self, request, *args, **kwargs):
        """
        Update only escalation fields for a level.
        """
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response({
            "message": "Escalation rule updated successfully",
            "data": serializer.data
        }, status=status.HTTP_200_OK)
    @action(detail=True, methods=['post'])
    def reset(self, request, pk=None):
        instance = self.get_object()
        instance.escalate_to = None
        instance.escalate_after_days = 0
        instance.escalate_after_hours = 0
        instance.escalate_after_minutes = 0
        instance.save()
        return Response({"message": "Escalation rule reset successfully"}, status=200)

class LoanEscalationRuleViewSet(viewsets.ModelViewSet):
    """
    API for managing escalation settings on each approval level.
    """
    serializer_class = LoanEscalationRuleSerializer
    queryset = LoanApprovalLevels.objects.all().order_by('loan_type', 'level')

    def get_queryset(self):
        queryset = super().get_queryset()
        request_type_id = self.request.query_params.get('loan_type')

        if request_type_id:
            queryset = queryset.filter(loan_type_id=request_type_id)
        
        return queryset.distinct()

    def update(self, request, *args, **kwargs):
        """
        Update only escalation fields for a level.
        """
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response({
            "message": "Escalation rule updated successfully",
            "data": serializer.data
        }, status=status.HTTP_200_OK)
    @action(detail=True, methods=['post'])
    def reset(self, request, pk=None):
        instance = self.get_object()
        instance.escalate_to = None
        instance.escalate_after_days = 0
        instance.escalate_after_hours = 0
        instance.escalate_after_minutes = 0
        instance.save()
        return Response({"message": "Escalation rule reset successfully"}, status=200)

class PayStructureViewSet(viewsets.ModelViewSet):
    queryset = PayStructure.objects.all()
    serializer_class = PayStructureSerializer
class PayslipLeaveViewSet(viewsets.ModelViewSet):
    queryset = PayslipLeave.objects.all()
    serializer_class = PayslipLeaveSerializer
