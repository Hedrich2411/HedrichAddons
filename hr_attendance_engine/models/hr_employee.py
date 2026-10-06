# -*- coding: utf-8 -*-
import pytz

from odoo import api, fields, models

from .hr_workday import date_range

OFFICER = 'hr_attendance.group_hr_attendance_officer'


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    compensatory_hours_balance = fields.Float(
        string='Compensatory balance',
        compute='_compute_compensatory_hours_balance',
        help="Approved compensatory overtime hours minus approved "
             "compensatory absences taken. Available to spend on "
             "compensatory-type permissions.",
    )

    def _compute_compensatory_hours_balance(self):
        # sudo: the balance is the employee's, whoever is allowed to see it.
        Overtime = self.env['hr.workday.overtime'].sudo()
        Absence = self.env['hr.absence'].sudo()
        for employee in self:
            earned = sum(Overtime.search([
                ('employee_id', '=', employee.id),
                ('type', '=', 'compensatory'),
                ('state', '=', 'approved'),
            ]).mapped('duration_hours'))
            spent = sum(Absence.search([
                ('employee_id', '=', employee.id),
                ('state', '=', 'approved'),
                ('type_id.consumes_compensatory_balance', '=', True),
            ]).mapped('duration_hours'))
            employee.compensatory_hours_balance = earned - spent

    # --- Smart buttons ---------------------------------------------------
    schedule_count = fields.Integer(compute='_compute_engine_counts', groups=OFFICER)
    absence_count = fields.Integer(compute='_compute_engine_counts', groups=OFFICER)
    overtime_count = fields.Integer(compute='_compute_engine_counts', groups=OFFICER)
    vacation_days_left = fields.Float(compute='_compute_engine_counts', groups=OFFICER)

    def _compute_engine_counts(self):
        def per_employee(model, aggregate, domain=()):
            return dict(self.env[model]._read_group(
                [('employee_id', 'in', self.ids), *domain], ['employee_id'], [aggregate]))

        schedules = per_employee('hr.schedule.assignment', 'schedule_id:count_distinct')
        absences = per_employee('hr.absence', '__count')
        overtime = per_employee('hr.workday.overtime', '__count')
        # Earned days still owed, expired ones included; the cycle being
        # earned is advance leave.
        vacation = per_employee('hr.vacation.period', 'days_remaining:sum', [
            ('state', 'in', ('open', 'expired')),
            ('cycle_end', '<', fields.Date.context_today(self))])
        for employee in self:
            employee.schedule_count = schedules.get(employee, 0)
            employee.absence_count = absences.get(employee, 0)
            employee.overtime_count = overtime.get(employee, 0)
            employee.vacation_days_left = vacation.get(employee, 0.0)

    # ------------------------------------------------------------------
    # Native attendance figures, sourced from the engine
    # ------------------------------------------------------------------
    def _compute_hours_last_month(self):
        """Month-to-date worked and paid overtime hours as the engine sees
        them: the stored snapshot of processed days, a dry run of the rest
        (typically today). Odoo's version sums hr.attendance.worked_hours,
        which subtracts the resource calendar's lunch.

        ``hours_today`` stays native: a live counter of raw punch time.
        """
        # sudo: like the compensatory balance, a figure about the employee.
        Workday = self.env['hr.workday'].sudo()
        now_utc = pytz.utc.localize(fields.Datetime.now())
        for timezone, employees in self.grouped('tz').items():
            today = now_utc.astimezone(pytz.timezone(timezone or 'UTC')).date()
            month_start = today.replace(day=1)
            results = Workday._engine_results(employees, month_start, today)
            overtime = dict(self.env['hr.workday.overtime'].sudo()._read_group([
                ('employee_id', 'in', employees.ids),
                ('state', '=', 'approved'), ('type', '=', 'paid'),
                ('date', '>=', month_start), ('date', '<=', today),
            ], ['employee_id'], ['duration_hours:sum']))
            for employee in employees:
                hours = sum(results[employee.id, day].worked_hours
                            for day in date_range(month_start, today))
                employee.hours_last_month = round(hours, 2)
                employee.hours_last_month_display = "%g" % employee.hours_last_month
                employee.hours_last_month_overtime = round(overtime.get(employee, 0.0), 2)

    def _compute_total_overtime(self):
        """Odoo's extra-hours balance, shown by the kiosk greeting: here,
        the compensatory hours the employee can still take off."""
        for employee in self:
            employee.total_overtime = employee.compensatory_hours_balance
