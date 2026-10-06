# -*- coding: utf-8 -*-
from odoo import api, fields, models


class HrScheduleAssignment(models.Model):
    _name = 'hr.schedule.assignment'
    _description = 'Daily Schedule Assignment'
    _order = 'date desc, employee_id'

    employee_id = fields.Many2one(
        'hr.employee', required=True, ondelete='cascade', index=True,
    )
    date = fields.Date(required=True, index=True)
    schedule_id = fields.Many2one(
        'hr.schedule', required=True, ondelete='restrict',
        check_company=True,
    )
    company_id = fields.Many2one(
        'res.company', related='employee_id.company_id',
        store=True, index=True,
    )

    _employee_date_unique = models.Constraint(
        'UNIQUE(employee_id, date)',
        'An employee can only have one schedule per day.',
    )

    @api.depends('employee_id', 'date', 'schedule_id')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = (
                f"{rec.employee_id.name or ''} "
                f"{rec.date or ''} - "
                f"{rec.schedule_id.name or ''}"
            ).strip()

    @api.model
    def _lines(self, employees, date_from, date_to):
        """``{(employee_id, date): hr.schedule.line}`` over the range: the
        line of the assigned schedule matching each date's weekday. Days
        without assignment, or whose schedule has no line for that weekday,
        are left out.
        """
        lines = {}
        for assignment in self.search([
            ('employee_id', 'in', employees.ids),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
        ]):
            weekday = str(assignment.date.weekday())
            line = assignment.schedule_id.line_ids.filtered(
                lambda l: l.day_of_week == weekday,
            )[:1]
            if line:
                lines[assignment.employee_id.id, assignment.date] = line
        return lines

    # ponytail: only the assigned day is invalidated; its neighbours' windows
    # shift too when shifts are close, reprocess them by hand if that bites.
    def _engine_touch(self):
        self.env['hr.workday']._engine_touched_days(
            (a.employee_id.id, a.date) for a in self
        )._engine_sources_changed()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._engine_touch()
        return records

    def write(self, vals):
        self._engine_touch()
        res = super().write(vals)
        self._engine_touch()
        return res

    def unlink(self):
        self._engine_touch()
        return super().unlink()
