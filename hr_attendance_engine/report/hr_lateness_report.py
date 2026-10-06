# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ReportHrLateness(models.AbstractModel):
    _name = 'report.hr_attendance_engine.report_lateness_document'
    _description = 'Lateness Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        """Same engine as the attendance report, keeping only the days
        where the employee was late, came back late from the break or
        left early.
        """
        data = data or {}
        attendance_report = self.env[
            'report.hr_attendance_engine.report_attendance_document'
        ]
        values = attendance_report._get_report_values(docids, data=data)

        blocks = []
        for block in values['blocks']:
            rows = [
                row
                for week in block['weeks']
                for row in week['rows']
                if row['raw']['late_total'] or row['raw']['early']
            ]
            if rows:
                blocks.append({
                    'employee': block['employee'],
                    'rows': rows,
                    'totals': attendance_report._totals(rows),
                })

        values['blocks'] = blocks
        return values
