# -*- coding: utf-8 -*-
from collections import OrderedDict

from odoo import api, fields, models


class ReportHrAbsence(models.AbstractModel):
    _name = 'report.hr_attendance_engine.report_absence_document'
    _description = 'Absence Report'

    def _build_employee_block(self, employee, date_from, date_to):
        """Every absence overlapping the range, plus a per-type recap."""
        absences = self.env['hr.absence'].search([
            ('employee_id', '=', employee.id),
            ('date_from', '<=', date_to),
            ('date_to', '>=', date_from),
        ], order='date_from')

        recap = OrderedDict()
        for absence in absences.filtered(lambda a: a.state == 'approved'):
            key = absence.type_id
            entry = recap.setdefault(key, {'days': 0.0, 'hours': 0.0, 'count': 0})
            entry['days'] += absence.duration_days
            entry['hours'] += absence.duration_hours
            entry['count'] += 1

        return {
            'employee': employee,
            'absences': absences,
            'recap': [
                {'type': atype, **totals} for atype, totals in recap.items()
            ],
            'totals': {
                'days': sum(v['days'] for v in recap.values()),
                'hours': sum(v['hours'] for v in recap.values()),
                'count': sum(v['count'] for v in recap.values()),
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
