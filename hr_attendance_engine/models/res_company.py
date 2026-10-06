# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    workday_tolerance_loss_mode = fields.Selection(
        [
            ('lenient', 'Lenient — only the excess counts as late'),
            ('strict', 'Strict — exceeding tolerance forfeits the grace; full delay counts'),
        ],
        string='Tolerance loss mode',
        default='lenient',
        required=True,
        help="How late_hours and early_leave_hours are computed when the "
             "worker exceeds the schedule's tolerance window.\n\n"
             "* Lenient: only the excess (delay - tolerance) becomes "
             "late_hours. The tolerance is always forgiven.\n"
             "* Strict: if the worker exceeds the tolerance, the grace "
             "is forfeited and the full delay from the planned entry "
             "counts as late_hours.",
    )
