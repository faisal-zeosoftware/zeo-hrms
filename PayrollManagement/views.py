from django.shortcuts import render
from .models import (SalaryComponent,EmployeeSalaryStructure,PayslipComponent,Payslip,PayrollRun,LoanType,LoanApplication,
                     LoanRepayment,LoanApprovalLevels,LoanApproval,PayslipApproval,PayslipCommonWorkflow,AdvanceSalaryRequest,AdvanceSalaryApproval,AdvanceCommonWorkflow,AirTicketPolicy,AirTicketAllocation,AirTicketRequest,
                     LoanEmailTemplate,LoanNotification,AdvanceSalaryEmailTemplate,AdvanceSalaryNotification)

from .serializer import (SalaryComponentSerializer,EmpBulkuploadSalaryStructureSerializer,EmployeeSalaryStructureSerializer,PayslipSerializer,PaySlipComponentSerializer,LoanTypeSerializer,LoanApplicationSerializer,LoanRepaymentSerializer,
                         LoanApprovalSerializer,LoanApprovalLevelsSerializer,PayrollRunSerializer,PayslipConfirmedSerializer,SIFSerializer,AdvanceSalaryRequestSerializer,AdvanceSalaryApprovalSerializer,AdvanceCommonWorkflowSerializer,PayslipCommonWorkflowSerializer,PayslipApprovalSerializer,AirTicketPolicySerializer,AirTicketAllocationSerializer
                         ,AirTicketRequestSerializer,LoanEmailTemplateSerializer,LoanNotificationSerializer,AdvSalaryEmailTemplateSerializer,AdvSalaryNotificationSerializer
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
            if excel_file.name.endswith('.xlsx'):
                try:
                    dataset = Dataset()
                    dataset.load(excel_file.read(), format='xlsx')
                    resource = EmployeeSalaryStructureResource()
                    all_errors = []
                    valid_rows = []
                    with transaction.atomic():
                        for row_idx, row in enumerate(dataset.dict, start=2):
                            row_errors = []
                            try:
                                resource.before_import_row(row, row_idx=row_idx)
                            except ValidationError as e:
                                row_errors.extend([f"Row {row_idx}: {error}" for error in e.messages])
                            if row_errors:
                                all_errors.extend(row_errors)
                            else:
                                valid_rows.append(row)

                    if all_errors:
                        return Response({"errors": all_errors}, status=400)

                    with transaction.atomic():
                        result = resource.import_data(dataset, dry_run=False, raise_errors=True)

                    return Response({"message": f"{result.total_rows} records created successfully"})
                except Exception as e:
                    return Response({"error": str(e)}, status=400)
            else:
                return Response({"error": "Invalid file format. Only Excel files (.xlsx) are supported."}, status=400)
        else:
            return Response({"error": "Please provide an Excel file."}, status=400)
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
                sif_data, total_salary = serializer.generate_sif_data()
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
                    'scr': scr_row
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
    queryset = AdvanceCommonWorkflow.objects.all()
    serializer_class = AdvanceCommonWorkflowSerializer

class AdvanceSalaryApprovalViewSet(viewsets.ModelViewSet):
    queryset = AdvanceSalaryApproval.objects.all()
    serializer_class = AdvanceSalaryApprovalSerializer

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
class AirTicketPolicyViewSet(viewsets.ModelViewSet):
    queryset = AirTicketPolicy.objects.all()
    serializer_class = AirTicketPolicySerializer
    # permission_classes = [IsAuthenticated]

class AirTicketAllocationViewSet(viewsets.ModelViewSet):
    queryset = AirTicketAllocation.objects.all()
    serializer_class = AirTicketAllocationSerializer
    # permission_classes = [IsAuthenticated]

    # def perform_create(self, serializer):
    #     # Manual allocation
    #     serializer.save(allocation_type='MANUAL', allocated_by=self.request.user.employee)  # Assuming user has an employee profile
    #     logger.info(f"Manual allocation created for Employee {serializer.instance.employee.id} by {self.request.user.id}")

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

    

class LoanEmailTemplateViewSet(viewsets.ModelViewSet):
    queryset = LoanEmailTemplate.objects.all()
    serializer_class = LoanEmailTemplateSerializer

class LoanNotificationViewSet(viewsets.ModelViewSet):
    queryset = LoanNotification.objects.all()
    serializer_class = LoanNotificationSerializer

class AdvSalaryEmailTemplateViewSet(viewsets.ModelViewSet):
    queryset = AdvanceSalaryEmailTemplate.objects.all()
    serializer_class = AdvSalaryEmailTemplateSerializer
class AdvSalaryNotificationViewSet(viewsets.ModelViewSet):
    queryset = AdvanceSalaryNotification.objects.all()
    serializer_class = AdvSalaryNotificationSerializer