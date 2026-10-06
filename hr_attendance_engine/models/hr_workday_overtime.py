# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .hr_absence import check_not_own


TYPE_SELECTION = [
    ('paid', 'Paid overtime'),
    ('compensatory', 'Compensatory (adds to balance)'),
]

STATE_SELECTION = [
    ('draft', 'Draft'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
]


class HrWorkdayOvertime(models.Model):
    _name = 'hr.workday.overtime'
    _description = 'Additional hours (overtime / compensatory)'
    _inherit = ['mail.thread']
    _order = 'date desc, employee_id, hour_from'

    name = fields.Char(compute='_compute_name', store=True)
    employee_id = fields.Many2one(
        'hr.employee', required=True, index=True, tracking=True,
    )
    date = fields.Date(
        required=True, tracking=True, index=True,
        help="Day this additional time belongs to.",
    )
    type = fields.Selection(
        TYPE_SELECTION, required=True, default='paid', tracking=True,
        help="• Paid overtime — feeds payroll as extra paid hours.\n"
             "• Compensatory — adds to the employee's compensatory balance, "
             "later consumed by compensatory absences.",
    )
    hour_from = fields.Float(required=True, help="Local hour (24h format).")
    hour_to = fields.Float(required=True, help="Local hour (24h format).")
    duration_hours = fields.Float(
        compute='_compute_duration', store=True,
        help="hour_to - hour_from.",
    )

    state = fields.Selection(
        STATE_SELECTION, default='draft', required=True, tracking=True,
        copy=False,
    )
    reason = fields.Text()
    approver_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_at = fields.Datetime(readonly=True, copy=False)

    workday_id = fields.Many2one(
        'hr.workday', ondelete='set null', index=True, readonly=True,
        help="Workday this overtime belongs to. Linked when either of the "
             "two is created, whichever comes last.",
    )
    company_id = fields.Many2one(
        'res.company', related='employee_id.company_id',
        store=True, index=True,
    )

    # ------------------------------------------------------------------
    # Computeds
    # ------------------------------------------------------------------
    @api.depends('employee_id', 'date', 'type', 'hour_from', 'hour_to')
    def _compute_name(self):
        for rec in self:
            label = dict(TYPE_SELECTION).get(rec.type, '')
            rec.name = (
                f"{rec.employee_id.name or ''} - {rec.date or ''} "
                f"({label}) {rec.hour_from:.1f}-{rec.hour_to:.1f}"
            ).strip()

    @api.depends('hour_from', 'hour_to')
    def _compute_duration(self):
        for rec in self:
            rec.duration_hours = max(0.0, rec.hour_to - rec.hour_from)

    def _link_workday(self):
        Workday = self.env['hr.workday']
        for rec in self:
            rec.workday_id = Workday.search([
                ('employee_id', '=', rec.employee_id.id),
                ('date', '=', rec.date),
            ], limit=1)

    def _check_workday_not_locked(self):
        """Approved overtime feeds the workday totals payroll consumed."""
        locked = self.workday_id.filtered(lambda w: w.state == 'locked')
        if locked:
            raise UserError(_(
                "%s is a locked workday. Unlock it before changing its "
                "additional hours.", locked[0].display_name,
            ))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._link_workday()
        records._check_workday_not_locked()
        return records

    def write(self, vals):
        self._check_workday_not_locked()
        res = super().write(vals)
        if 'employee_id' in vals or 'date' in vals:
            self._link_workday()
        self._check_workday_not_locked()
        return res

    def unlink(self):
        self._check_workday_not_locked()
        return super().unlink()

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('hour_from', 'hour_to')
    def _check_hours(self):
        for rec in self:
            if rec.hour_to <= rec.hour_from:
                raise ValidationError(_(
                    "'Hour to' must be after 'Hour from'.",
                ))
            if not (0.0 <= rec.hour_from < 24.0 and 0.0 < rec.hour_to <= 24.0):
                raise ValidationError(_(
                    "Hours must be between 00:00 and 24:00.",
                ))

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_approve(self):
        check_not_own(self)
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_(
                    "Only draft records can be approved.",
                ))
            rec.write({
                'state': 'approved',
                'approver_id': self.env.uid,
                'approved_at': fields.Datetime.now(),
            })
        return True

    def action_reject(self):
        for rec in self:
            if rec.state == 'rejected':
                continue
            rec.write({
                'state': 'rejected',
                'approver_id': self.env.uid,
            })
        return True

    def action_reset_to_draft(self):
        for rec in self:
            rec.write({
                'state': 'draft',
                'approver_id': False,
                'approved_at': False,
            })
        return True
