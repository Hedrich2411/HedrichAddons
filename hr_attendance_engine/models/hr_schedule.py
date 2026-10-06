# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


DAY_OF_WEEK_SELECTION = [
    ('0', 'Monday'),
    ('1', 'Tuesday'),
    ('2', 'Wednesday'),
    ('3', 'Thursday'),
    ('4', 'Friday'),
    ('5', 'Saturday'),
    ('6', 'Sunday'),
]


class HrSchedule(models.Model):
    _name = 'hr.schedule'
    _description = 'Work Schedule Template'
    _order = 'name'

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True,
    )
    color = fields.Integer()

    line_ids = fields.One2many(
        'hr.schedule.line', 'schedule_id', string='Lines', copy=True,
    )

    def _engine_invalidate(self):
        """The template changed: processed workdays that use it go back to
        draft. Locked ones keep their frozen snapshot, so editing a template
        already used by a closed payroll is not blocked.
        """
        assignments = self.env['hr.schedule.assignment'].search([
            ('schedule_id', 'in', self.ids),
        ])
        self.env['hr.workday']._engine_touched_days(
            (a.employee_id.id, a.date) for a in assignments
        ).filtered(lambda w: w.state == 'processed').write({'state': 'draft'})


class HrScheduleLine(models.Model):
    _name = 'hr.schedule.line'
    _description = 'Work Schedule Line'
    _order = 'schedule_id, day_of_week'

    schedule_id = fields.Many2one(
        'hr.schedule', required=True, ondelete='cascade', index=True,
    )
    day_of_week = fields.Selection(
        DAY_OF_WEEK_SELECTION, required=True, string='Day of week',
    )
    is_rest_day = fields.Boolean(
        help="If set, the worker is not expected to work on this day. "
             "Entry/exit and break fields are ignored.",
    )

    planned_entry = fields.Float(string='Entry', help="Hour of the day (24h).")
    planned_exit = fields.Float(string='Exit', help="Hour of the day (24h).")
    crosses_midnight = fields.Boolean(
        compute='_compute_crosses_midnight', store=True,
        help="True when exit falls before entry: shift ends the next day.",
    )

    entry_tolerance_minutes = fields.Integer(
        string='Entry tolerance (min)', default=0,
    )
    exit_tolerance_minutes = fields.Integer(
        string='Exit tolerance (min)', default=0,
    )

    has_break = fields.Boolean(string='Has break')
    break_start = fields.Float(string='Break start')
    break_end = fields.Float(string='Break end')

    _schedule_dayofweek_unique = models.Constraint(
        'UNIQUE(schedule_id, day_of_week)',
        'A schedule can have only one line per day of the week.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.schedule_id._engine_invalidate()
        return lines

    def write(self, vals):
        res = super().write(vals)
        self.schedule_id._engine_invalidate()
        return res

    def unlink(self):
        schedules = self.schedule_id
        res = super().unlink()
        schedules._engine_invalidate()
        return res

    def _planned_hours(self):
        """Working hours of the line: entry to exit, past midnight when
        the shift crosses it, minus the break."""
        self.ensure_one()
        if self.is_rest_day:
            return 0.0
        hours = (self.planned_exit - self.planned_entry) % 24
        if self.has_break:
            hours -= self.break_end - self.break_start
        return hours

    @api.depends('planned_entry', 'planned_exit', 'is_rest_day')
    def _compute_crosses_midnight(self):
        for rec in self:
            rec.crosses_midnight = (
                not rec.is_rest_day
                and rec.planned_exit < rec.planned_entry
            )

    @api.constrains(
        'is_rest_day', 'planned_entry', 'planned_exit',
        'has_break', 'break_start', 'break_end',
    )
    def _check_hours(self):
        for rec in self:
            if rec.is_rest_day:
                continue
            for hour, label in (
                (rec.planned_entry, _("Entry")),
                (rec.planned_exit, _("Exit")),
            ):
                if not 0.0 <= hour < 24.0:
                    raise ValidationError(_(
                        "%(label)s must be between 00:00 and 23:59.",
                        label=label,
                    ))
            if rec.planned_entry == rec.planned_exit:
                raise ValidationError(_("Entry and Exit cannot be equal."))
            if rec.has_break:
                for hour, label in (
                    (rec.break_start, _("Break start")),
                    (rec.break_end, _("Break end")),
                ):
                    if not 0.0 <= hour < 24.0:
                        raise ValidationError(_(
                            "%(label)s must be between 00:00 and 23:59.",
                            label=label,
                        ))
                if rec.break_end <= rec.break_start:
                    raise ValidationError(_("Break end must be after break start."))
