# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    workday_tolerance_loss_mode = fields.Selection(
        related='company_id.workday_tolerance_loss_mode',
        readonly=False,
    )
