# -*- coding: utf-8 -*-
from odoo import fields, models


class IrUiView(models.Model):
    _inherit = 'ir.ui.view'

    type = fields.Selection(
        selection_add=[('attendance_review', 'Attendance Review')],
        ondelete={'attendance_review': 'cascade'},
    )

    def _get_view_info(self):
        # Registers the type in session.view_info so the JS view registry
        # accepts it; without this the client rejects the arch.
        return {
            **super()._get_view_info(),
            'attendance_review': {'icon': 'fa fa-check-square-o'},
        }
