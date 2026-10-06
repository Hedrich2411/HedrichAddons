# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


STATE_SELECTION = [
    ('open', 'Open'),
    ('exhausted', 'Exhausted'),
    ('expired', 'Expired'),
    ('closed', 'Closed'),
]


class HrVacationPeriod(models.Model):
    _name = 'hr.vacation.period'
    _description = 'Vacation Period'
    _order = 'cycle_start desc, employee_id'

    employee_id = fields.Many2one(
        'hr.employee', required=True, ondelete='cascade', index=True,
    )
    name = fields.Char(compute='_compute_name', store=True)
    cycle_start = fields.Date(
        required=True,
        help="Start of the employee's vacation cycle (typically the "
             "anniversary of their contract start).",
    )
    cycle_end = fields.Date(required=True)
    expiry_date = fields.Date(
        help="Date by which the days should be taken, one year after the "
             "cycle by default. Days left after it are not lost: they can "
             "still be taken, are taken first, and the period shows as "
             "Expired so payroll can pay the indemnity Peruvian law owes.",
    )
    days_earned = fields.Integer(
        default=30,
        help="Days available for this cycle. Peruvian law: 30 days.",
    )
    days_paid_cash = fields.Float(
        default=0.0,
        help="Days sold in cash instead of taken as leave. "
             "Peruvian law caps this at 50% of earned.",
    )
    days_taken = fields.Float(compute='_compute_consumption', store=True)
    days_remaining = fields.Float(compute='_compute_consumption', store=True)
    is_closed = fields.Boolean(
        copy=False, help="Closed by hand: its days can no longer be taken.",
    )
    state = fields.Selection(
        STATE_SELECTION, compute='_compute_state', store=True,
        help="Open: days can still be taken. Exhausted: none left. "
             "Expired: days still owed past the expiry date. Closed: closed "
             "by hand.",
    )
    absence_ids = fields.One2many('hr.absence', 'vacation_period_id')
    company_id = fields.Many2one(
        'res.company', related='employee_id.company_id',
        store=True, index=True,
    )

    _employee_cycle_unique = models.Constraint(
        'UNIQUE(employee_id, cycle_start)',
        'An employee cannot have two vacation periods starting on the same date.',
    )

    @api.depends('employee_id', 'cycle_start', 'cycle_end')
    def _compute_name(self):
        for rec in self:
            if rec.cycle_start and rec.cycle_end:
                rec.name = f"{rec.cycle_start.year}-{rec.cycle_end.year}"
            else:
                rec.name = "—"

    @api.depends('absence_ids.state', 'absence_ids.duration_days',
                 'days_earned', 'days_paid_cash')
    def _compute_consumption(self):
        for rec in self:
            taken = sum(
                a.duration_days for a in rec.absence_ids
                if a.state == 'approved'
            )
            rec.days_taken = taken
            rec.days_remaining = rec.days_earned - taken - rec.days_paid_cash

    @api.depends('is_closed', 'expiry_date', 'days_remaining')
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.is_closed:
                rec.state = 'closed'
            elif rec.days_remaining <= 0:
                rec.state = 'exhausted'
            elif rec.expiry_date and rec.expiry_date < today:
                rec.state = 'expired'
            else:
                rec.state = 'open'

    @api.model
    def _cron_expire(self):
        """Expiry depends on today, which no field change signals."""
        expired = self.search([
            ('state', '=', 'open'),
            ('expiry_date', '<', fields.Date.context_today(self)),
        ])
        self.env.add_to_compute(self._fields['state'], expired)
        expired.flush_recordset(['state'])

    @api.onchange('cycle_start')
    def _onchange_cycle_start(self):
        if self.cycle_start:
            self.cycle_end = self.cycle_start + relativedelta(years=1, days=-1)
            self.expiry_date = self.cycle_end + relativedelta(years=1)

    @api.constrains('cycle_start', 'cycle_end', 'days_earned',
                    'days_paid_cash')
    def _check_period(self):
        for rec in self:
            if rec.cycle_start > rec.cycle_end:
                raise ValidationError(_(
                    "Cycle end must be after cycle start.",
                ))
            if rec.days_earned <= 0:
                raise ValidationError(_(
                    "Days earned must be positive.",
                ))
            if rec.days_paid_cash < 0:
                raise ValidationError(_(
                    "Days paid in cash cannot be negative.",
                ))
            if rec.days_paid_cash > rec.days_earned / 2.0:
                raise ValidationError(_(
                    "Days paid in cash cannot exceed 50%% of days earned "
                    "(Peruvian labor law).",
                ))

    def action_close(self):
        self.write({'is_closed': True})
        return True

    def action_reopen(self):
        self.write({'is_closed': False})
        return True
