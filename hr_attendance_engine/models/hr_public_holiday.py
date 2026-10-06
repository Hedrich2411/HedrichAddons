# -*- coding: utf-8 -*-
from odoo import api, fields, models


class HrPublicHoliday(models.Model):
    _name = 'hr.public.holiday'
    _description = 'Public Holiday'
    _order = 'date desc'

    name = fields.Char(required=True, translate=True)
    date = fields.Date(required=True, index=True)
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company,
        help="Leave empty to apply the holiday to every company.",
    )
    active = fields.Boolean(default=True)

    _date_company_unique = models.Constraint(
        'UNIQUE(date, company_id)',
        'There is already a public holiday on that date for this company.',
    )

    def _engine_touch(self):
        Workday = self.env['hr.workday']
        for holiday in self:
            domain = [('date', '=', holiday.date), ('state', '!=', 'draft')]
            if holiday.company_id:
                domain.append(('company_id', '=', holiday.company_id.id))
            Workday.search(domain)._engine_sources_changed()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._engine_touch()
        return records

    def write(self, vals):
        engine = bool({'date', 'company_id', 'active'} & set(vals))
        if engine:
            self._engine_touch()
        res = super().write(vals)
        if engine:
            self._engine_touch()
        return res

    def unlink(self):
        self._engine_touch()
        return super().unlink()
