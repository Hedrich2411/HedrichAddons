# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .hr_absence_type import UNIT_SELECTION
from .hr_workday import MANAGER_GROUP, date_range


STATE_SELECTION = [
    ('draft', 'Draft'),
    ('confirmed', 'Confirmed'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('cancelled', 'Cancelled'),
]

# ponytail: days with no schedule assigned yet (a leave planned ahead)
# count this many hours; approval recomputes against the real schedule.
FALLBACK_DAY_HOURS = 8.0

# Fields the engine reads from an approved absence.
ENGINE_FIELDS = {'state', 'employee_id', 'type_id', 'date_from', 'date_to',
                 'unit', 'hour_from', 'hour_to', 'half_day_period'}


def check_not_own(records):
    """Nobody approves their own request, except attendance administrators."""
    env = records.env
    if env.su or env.user.has_group(MANAGER_GROUP):
        return
    if records.sudo().filtered(lambda r: r.employee_id.user_id == env.user):
        raise AccessError(env._(
            "You cannot approve your own request. Ask another officer."))


class HrAbsence(models.Model):
    _name = 'hr.absence'
    _description = 'Absence Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_from desc, employee_id'

    name = fields.Char(compute='_compute_name', store=True)
    employee_id = fields.Many2one(
        'hr.employee', required=True, index=True, tracking=True,
    )
    type_id = fields.Many2one(
        'hr.absence.type', required=True, tracking=True,
    )
    category = fields.Selection(
        related='type_id.category', store=True,
    )
    consumes_vacation = fields.Boolean(
        related='type_id.consumes_vacation_period',
    )
    consumes_compensatory = fields.Boolean(
        related='type_id.consumes_compensatory_balance',
    )
    requires_document = fields.Boolean(
        related='type_id.requires_document',
    )

    date_from = fields.Date(required=True, tracking=True)
    date_to = fields.Date(required=True, tracking=True)
    unit = fields.Selection(
        UNIT_SELECTION, required=True, default='day',
    )
    hour_from = fields.Float(
        help="For hourly absences, start time on each day of the range "
             "(local hour). A range of days repeats the same hours daily, "
             "e.g. the lactation hour.",
    )
    hour_to = fields.Float(
        help="For hourly absences, end time on each day of the range (local hour).",
    )
    half_day_period = fields.Selection(
        [('am', 'Morning'), ('pm', 'Afternoon')],
        string='Half', default='am',
        help="For half-day absences: the half of the shift before the "
             "break (morning) or after it (afternoon).",
    )

    duration_days = fields.Float(
        compute='_compute_duration', store=True,
        help="Calendar days, for payroll and vacation period deduction.",
    )
    duration_hours = fields.Float(
        compute='_compute_duration', store=True,
        help="Working hours the absence covers according to the employee's "
             "assigned schedule (rest days count 0).",
    )

    state = fields.Selection(
        STATE_SELECTION, required=True, default='draft',
        tracking=True, copy=False,
    )
    reason = fields.Text()
    attachment_ids = fields.Many2many(
        'ir.attachment', string='Documents',
    )

    vacation_period_id = fields.Many2one(
        'hr.vacation.period', readonly=True, copy=False, ondelete='restrict',
        check_company=True,
    )
    approver_id = fields.Many2one(
        'res.users', readonly=True, copy=False,
    )
    approved_at = fields.Datetime(readonly=True, copy=False)

    company_id = fields.Many2one(
        'res.company', related='employee_id.company_id',
        store=True, index=True,
    )

    # ------------------------------------------------------------------
    # Computeds
    # ------------------------------------------------------------------
    @api.depends('employee_id', 'type_id', 'date_from')
    def _compute_name(self):
        for rec in self:
            parts = []
            if rec.employee_id:
                parts.append(rec.employee_id.name)
            if rec.type_id:
                parts.append(rec.type_id.name)
            if rec.date_from:
                parts.append(str(rec.date_from))
            rec.name = ' / '.join(parts) or '—'

    @api.depends('employee_id', 'date_from', 'date_to', 'unit',
                 'hour_from', 'hour_to')
    def _compute_duration(self):
        for rec in self:
            if not (rec.employee_id and rec.date_from and rec.date_to) or rec.date_from > rec.date_to:
                rec.duration_days = rec.duration_hours = 0.0
                continue
            per_date = rec._per_date()
            rec.duration_days = sum(days for _day, days, _hours in per_date)
            rec.duration_hours = sum(hours for _day, _days, hours in per_date)

    def _per_date(self):
        """``[(date, days, hours)]`` the absence takes on each date of its
        range, after the employee's assigned schedule. Leave counts calendar
        days, as Peruvian law does; hours follow the shift, so a rest day
        takes none and an hourly permit only applies where work is planned.
        """
        self.ensure_one()
        lines = self.env['hr.schedule.assignment']._lines(
            self.employee_id, self.date_from, self.date_to)
        out = []
        for day in date_range(self.date_from, self.date_to):
            line = lines.get((self.employee_id.id, day))
            planned = line._planned_hours() if line else FALLBACK_DAY_HOURS
            if self.unit == 'hour':
                hours = max(0.0, self.hour_to - self.hour_from) if planned else 0.0
                out.append((day, hours / planned if planned else 0.0, hours))
            else:
                share = 0.5 if self.unit == 'half_day' else 1.0
                out.append((day, share, planned * share))
        return out

    @api.onchange('type_id')
    def _onchange_type(self):
        if self.type_id:
            self.unit = self.type_id.default_unit

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('date_from', 'date_to', 'unit', 'hour_from', 'hour_to')
    def _check_dates(self):
        for rec in self:
            if rec.date_from > rec.date_to:
                raise ValidationError(_(
                    "'Date from' must be on or before 'Date to'.",
                ))
            if rec.unit == 'hour':
                if rec.hour_to <= rec.hour_from:
                    raise ValidationError(_(
                        "'Hour to' must be after 'Hour from'.",
                    ))
                if not (0.0 <= rec.hour_from < 24.0 and 0.0 <= rec.hour_to <= 24.0):
                    raise ValidationError(_(
                        "Hours must be between 00:00 and 24:00.",
                    ))

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_confirm(self):
        for rec in self:
            if rec.state == 'draft':
                rec.state = 'confirmed'
        return True

    def action_approve(self):
        check_not_own(self)
        # The schedule may have been assigned after the request was drafted.
        self.env.add_to_compute(self._fields['duration_days'], self)
        self.env.add_to_compute(self._fields['duration_hours'], self)
        for rec in self:
            if rec.state != 'confirmed':
                raise UserError(_(
                    "Only confirmed absences can be approved.",
                ))
            if rec.requires_document and not rec.attachment_ids:
                raise UserError(_(
                    "%s requires a supporting document.",
                    rec.type_id.name,
                ))
            pieces = rec._take_vacation_days() if rec.consumes_vacation else []
            if rec.type_id.consumes_compensatory_balance:
                balance = rec.employee_id.compensatory_hours_balance
                if balance < rec.duration_hours:
                    raise UserError(_(
                        "%(emp)s has %(bal).1f compensatory hours but the "
                        "request is for %(req).1f.",
                        emp=rec.employee_id.name,
                        bal=balance,
                        req=rec.duration_hours,
                    ))
            approval = {
                'state': 'approved',
                'approver_id': self.env.uid,
                'approved_at': fields.Datetime.now(),
            }
            rec.write(approval)
            for period, first, last in pieces[1:]:
                rec.copy({**approval, 'date_from': first, 'date_to': last,
                          'vacation_period_id': period.id})
            if len(pieces) > 1:
                rec.message_post(body=_(
                    "Split between vacation periods: %s.",
                    ', '.join(f"{period.name} ({first} → {last})"
                              for period, first, last in pieces),
                ))
        return True

    def action_reject(self):
        for rec in self:
            if rec.state not in ('confirmed', 'approved'):
                raise UserError(_(
                    "Only confirmed or approved absences can be rejected.",
                ))
            rec.write({
                'state': 'rejected',
                'vacation_period_id': False,
                'approver_id': self.env.uid,
            })
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state == 'cancelled':
                continue
            rec.write({
                'state': 'cancelled',
                'vacation_period_id': False,
            })
        return True

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state in ('approved',):
                raise UserError(_(
                    "Cancel the approval before resetting to draft.",
                ))
            rec.write({
                'state': 'draft',
                'vacation_period_id': False,
                'approver_id': False,
                'approved_at': False,
            })
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _take_vacation_days(self):
        """Draw the leave from the employee's periods, oldest first.

        Expired days are still owed under Peruvian law, so they go first,
        and the cycle still being earned can lend days in advance. When a
        period runs out midway, the leave keeps the dates it covers and the
        rest goes to the next period, each piece imputed to a single period.
        Returns the pieces as ``[(period, first date, last date)]``; the
        first one is this record, the others are left to the caller.
        """
        self.ensure_one()
        periods = self.env['hr.vacation.period'].search([
            ('employee_id', '=', self.employee_id.id),
            ('cycle_start', '<=', self.date_from),
            ('state', 'in', ('open', 'expired')),
        ], order='cycle_start')
        left = {period: period.days_remaining for period in periods}
        pieces = []
        for day, days, _hours in self._per_date():
            while periods and left[periods[0]] < days - 1e-6:
                periods = periods[1:]
            if not periods:
                raise UserError(_(
                    "%(emp)s has %(rem).1f vacation days left but the "
                    "request is for %(req).1f.",
                    emp=self.employee_id.name,
                    rem=sum(p.days_remaining for p in left),
                    req=self.duration_days,
                ))
            left[periods[0]] -= days
            if pieces and pieces[-1][0] == periods[0]:
                pieces[-1][2] = day
            else:
                pieces.append([periods[0], day, day])
        self.write({'vacation_period_id': pieces[0][0].id, 'date_to': pieces[0][2]})
        return pieces

    # ------------------------------------------------------------------
    # Engine sync
    # ------------------------------------------------------------------
    def _engine_workdays(self):
        """Non-draft workdays the approved ones among these absences cover."""
        return self.env['hr.workday']._engine_touched_days(
            (a.employee_id.id, day)
            for a in self.filtered(lambda a: a.state == 'approved')
            for day in date_range(a.date_from, a.date_to)
        )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._engine_workdays()._engine_sources_changed()
        return records

    def write(self, vals):
        engine = bool(ENGINE_FIELDS & set(vals))
        if engine:
            self._engine_workdays()._engine_sources_changed()
        res = super().write(vals)
        if engine:
            self._engine_workdays()._engine_sources_changed()
        return res

    def unlink(self):
        self._engine_workdays()._engine_sources_changed()
        return super().unlink()
