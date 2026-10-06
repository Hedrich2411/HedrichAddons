# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..models.hr_workday import date_range


class HrScheduleAssignmentWizard(models.TransientModel):
    _name = 'hr.schedule.assignment.wizard'
    _description = 'Bulk Schedule Assignment'

    employee_ids = fields.Many2many('hr.employee', required=True)
    schedule_id = fields.Many2one('hr.schedule', required=True)
    date_from = fields.Date(required=True, default=fields.Date.context_today)
    date_to = fields.Date(required=True, default=fields.Date.context_today)

    overwrite_existing = fields.Boolean(
        default=False,
        help="If unchecked, existing assignments for the same (employee, date) "
             "are kept and skipped silently. If checked, they are replaced.",
    )

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for wiz in self:
            if wiz.date_from and wiz.date_to and wiz.date_from > wiz.date_to:
                raise ValidationError(_("'Date from' must be on or before 'Date to'."))

    def action_assign(self):
        self.ensure_one()
        if not self.employee_ids:
            raise UserError(_("Select at least one employee."))

        Assignment = self.env['hr.schedule.assignment']
        dates = date_range(self.date_from, self.date_to)
        if not dates:
            raise UserError(_("The selected range yields no eligible dates."))

        existing = Assignment.search([
            ('employee_id', 'in', self.employee_ids.ids),
            ('date', 'in', dates),
        ])
        existing_by_key = {(a.employee_id.id, a.date): a for a in existing}

        to_create = []
        replaced = 0
        skipped = 0
        for employee in self.employee_ids:
            for date in dates:
                key = (employee.id, date)
                if key in existing_by_key:
                    if self.overwrite_existing:
                        existing_by_key[key].schedule_id = self.schedule_id
                        replaced += 1
                    else:
                        skipped += 1
                else:
                    to_create.append({
                        'employee_id': employee.id,
                        'date': date,
                        'schedule_id': self.schedule_id.id,
                    })

        created = Assignment.create(to_create) if to_create else Assignment

        message = _(
            "Created: %(created)s · Replaced: %(replaced)s · Skipped: %(skipped)s",
            created=len(created), replaced=replaced, skipped=skipped,
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Schedule assignment"),
                'message': message,
                'type': 'success' if (created or replaced) else 'warning',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
