# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ReportHrVacation(models.AbstractModel):
    _name = 'report.hr_attendance_engine.report_vacation_document'
    _description = 'Vacation Report'

    def _build_employee_block(self, employee, date_from, date_to):
        """Vacation periods of the employee plus the vacation absences
        that fall inside the requested range.
        """
        periods = self.env['hr.vacation.period'].search(
            [('employee_id', '=', employee.id)], order='cycle_start',
        )
        absences = self.env['hr.absence'].search([
            ('employee_id', '=', employee.id),
            ('type_id.consumes_vacation_period', '=', True),
            ('date_from', '<=', date_to),
            ('date_to', '>=', date_from),
        ], order='date_from')
        return {
            'employee': employee,
            'periods': periods,
            'absences': absences,
            'totals': {
                'earned': sum(periods.mapped('days_earned')),
                'taken': sum(periods.mapped('days_taken')),
                'cash': sum(periods.mapped('days_paid_cash')),
                'remaining': sum(periods.mapped('days_remaining')),
                'in_range': sum(
                    a.duration_days for a in absences if a.state == 'approved'
                ),
            },
        }

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        employees = self.env['hr.employee'].browse(data.get('employee_ids', []))
        date_from = fields.Date.to_date(data.get('date_from'))
        date_to = fields.Date.to_date(data.get('date_to'))
        company = self.env.company
        return {
            'doc_model': 'hr.employee',
            'docs': employees,
            'date_from': date_from,
            'date_to': date_to,
            'printed_on': fields.Datetime.now(),
            'blocks': [self._build_employee_block(emp, date_from, date_to)
                       for emp in employees],
            'company': company,
            'brand': company.primary_color or '#875A7B',
        }
