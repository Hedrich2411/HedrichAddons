# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


REPORT_ACTIONS = {
    'attendance': 'hr_attendance_engine.action_report_attendance',
    'lateness': 'hr_attendance_engine.action_report_lateness',
    'absence': 'hr_attendance_engine.action_report_absence',
    'vacation': 'hr_attendance_engine.action_report_vacation',
}


class HrAttendanceReportWizard(models.TransientModel):
    _name = 'hr.attendance.report.wizard'
    _description = 'Attendance Report Wizard'

    report_type = fields.Selection(
        [
            ('attendance', 'Attendance'),
            ('lateness', 'Lateness'),
            ('absence', 'Absences'),
            ('vacation', 'Vacation'),
        ],
        required=True, default='attendance',
    )
    scope = fields.Selection(
        [
            ('all', 'All employees'),
            ('department', 'By department'),
            ('employee', 'By employee'),
        ],
        required=True, default='employee',
    )
    department_ids = fields.Many2many(
        'hr.department',
        help="Sub-departments are included automatically.",
    )
    employee_ids = fields.Many2many('hr.employee')
    date_from = fields.Date(required=True, default=fields.Date.context_today)
    date_to = fields.Date(required=True, default=fields.Date.context_today)

    @api.model
    def default_get(self, fields_list):
        """Preselect the employees the wizard was launched from (Print
        menu on the form or on a list selection).
        """
        res = super().default_get(fields_list)
        if self.env.context.get('active_model') == 'hr.employee':
            active_ids = self.env.context.get('active_ids') or []
            if active_ids:
                res.setdefault('scope', 'employee')
                res.setdefault('employee_ids', [(6, 0, active_ids)])
        return res

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for wiz in self:
            if wiz.date_from > wiz.date_to:
                raise ValidationError(_("'Date from' must be on or before 'Date to'."))

    def _get_employees(self):
        """Resolve the scope into an hr.employee recordset.

        'child_of' walks the department tree, so picking a parent
        department pulls in every employee below it.
        """
        self.ensure_one()
        Employee = self.env['hr.employee']
        if self.scope == 'all':
            return Employee.search([])
        if self.scope == 'department':
            if not self.department_ids:
                raise UserError(_("Select at least one department."))
            return Employee.search([
                ('department_id', 'child_of', self.department_ids.ids),
            ])
        if not self.employee_ids:
            raise UserError(_("Select at least one employee."))
        return self.employee_ids

    def action_print(self):
        self.ensure_one()
        employees = self._get_employees()
        if not employees:
            raise UserError(_("No employee matches the selected scope."))
        # ponytail: computed on the fly, no hr.workday rows are created.
        return self.env.ref(
            REPORT_ACTIONS[self.report_type]
        ).report_action(employees, data={
            'employee_ids': employees.ids,
            'date_from': self.date_from,
            'date_to': self.date_to,
        })
