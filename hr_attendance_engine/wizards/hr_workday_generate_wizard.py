# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class HrWorkdayGenerateWizard(models.TransientModel):
    _name = 'hr.workday.generate.wizard'
    _description = 'Generate Workdays for a Range'

    employee_ids = fields.Many2many('hr.employee', required=True)
    date_from = fields.Date(required=True, default=fields.Date.context_today)
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    only_assigned = fields.Boolean(
        default=True,
        help="If checked, restrict to employees that actually have a "
             "schedule assignment in the date range. Days with no "
             "assignment for an unselected employee are skipped.",
    )

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for wiz in self:
            if wiz.date_from and wiz.date_to and wiz.date_from > wiz.date_to:
                raise ValidationError(_("'Date from' must be on or before 'Date to'."))

    def action_generate(self):
        self.ensure_one()
        if not self.employee_ids:
            raise UserError(_("Select at least one employee."))

        employees = self.employee_ids
        if self.only_assigned:
            assigned_ids = self.env['hr.schedule.assignment'].search([
                ('employee_id', 'in', employees.ids),
                ('date', '>=', self.date_from),
                ('date', '<=', self.date_to),
            ]).employee_id.ids
            employees = employees.filtered(lambda e: e.id in assigned_ids)
            if not employees:
                raise UserError(_(
                    "None of the selected employees have a schedule "
                    "assignment in this range. Uncheck 'Only assigned' to "
                    "process them anyway (they'll be classified as 'No schedule').",
                ))

        summary = self.env['hr.workday']._generate(
            employees, self.date_from, self.date_to,
        )

        message = _(
            "Created: %(created)s · Processed: %(processed)s · Skipped (locked): %(skipped)s",
            created=summary['created'],
            processed=summary['processed'],
            skipped=summary['skipped_locked'],
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Workday generation"),
                'message': message,
                'type': 'success' if summary['processed'] else 'warning',
                'sticky': False,
                'next': {
                    'type': 'ir.actions.act_window',
                    'res_model': 'hr.workday',
                    'views': [(False, 'list'), (False, 'form')],
                    'view_mode': 'list,form',
                    'domain': [('id', 'in', summary['workdays'].ids)],
                    'name': _("Generated Workdays"),
                },
            },
        }
