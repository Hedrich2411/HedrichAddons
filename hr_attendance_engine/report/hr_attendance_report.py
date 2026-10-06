# -*- coding: utf-8 -*-
import pytz

from odoo import api, fields, models
from odoo.tools.misc import format_date

from ..models.hr_workday import date_range


class ReportHrAttendance(models.AbstractModel):
    # Must be exactly "report." + the report_name of the ir.actions.report,
    # that's how Odoo looks up the rendering model.
    _name = 'report.hr_attendance_engine.report_attendance_document'
    _description = 'Attendance Report'

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _fmt_hours(value):
        """Float hours -> 'HH:MM:SS'."""
        total = int(round((value or 0.0) * 3600))
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"

    @staticmethod
    def _fmt_dt(dt, tz):
        """Naive UTC datetime -> local 'HH:MM'."""
        if not dt:
            return ''
        return pytz.utc.localize(dt).astimezone(tz).strftime('%H:%M')

    # ------------------------------------------------------------------
    # Row building
    # ------------------------------------------------------------------
    def _build_row(self, workday, tz, overtime):
        """One report line from an engine result (stored snapshot or dry
        run, see hr.workday._engine_results)."""
        attendances = workday.attendance_ids.sorted('check_in')

        def span(start, end):
            return (end - start).total_seconds() / 3600.0 if start and end else 0.0

        # Punch hours: real span minus real break, NOT clipped to the
        # planned window (worked_hours is the clipped one).
        brk = span(workday.real_break_start_dt, workday.real_break_end_dt)
        punch = max(0.0, span(workday.real_entry_dt, workday.real_exit_dt) - brk)
        # How much longer than planned the break ran.
        planned_brk = span(workday.planned_break_start_dt, workday.planned_break_end_dt)
        late_break = max(0.0, brk - planned_brk) if brk and planned_brk else 0.0

        raw = {
            'expected': workday.expected_hours,
            'worked': workday.worked_hours,
            'punch': punch,
            'brk': brk,
            'late': workday.late_hours,
            'late_break': late_break,
            'late_total': workday.late_hours + late_break,
            'early': workday.early_leave_hours,
            'discount': workday.unauthorized_absence_hours,
            'holiday': workday.holiday_worked_hours,
            'leave': workday.leave_hours,
            'ot_paid': sum(o.duration_hours for o in overtime if o.type == 'paid'),
            'ot_comp': sum(o.duration_hours
                           for o in overtime if o.type == 'compensatory'),
        }

        row = {k: self._fmt_hours(v) for k, v in raw.items()}
        row.update({
            'raw': raw,
            'date': workday.date,
            # Babel gives the weekday already translated to the user's lang.
            'day_name': format_date(self.env, workday.date, date_format='EEEE'),
            # Two rows in the cell: the CSS uses white-space: pre-line.
            'schedule': workday._engine_schedule_label(tz, sep='\n'),
            'entries': ', '.join(self._fmt_dt(a.check_in, tz)
                                 for a in attendances),
            'exits': ', '.join(self._fmt_dt(a.check_out, tz)
                               for a in attendances if a.check_out),
            'day_type': dict(
                workday._fields['day_type']._description_selection(self.env)
            ).get(workday.day_type, ''),
            'absence_types': workday.absence_ids.type_id,
            'absence_codes': ', '.join(workday.absence_ids.mapped('type_id.code')),
            'is_absent': workday.is_absent,
            'incongruency': workday.incongruency_message or '',
        })
        return row

    def _totals(self, rows):
        keys = rows[0]['raw'].keys() if rows else []
        return {k: self._fmt_hours(sum(r['raw'][k] for r in rows))
                for k in keys}

    def _build_employee_block(self, employee, date_from, date_to, results, overtime):
        """Rows grouped by ISO week, each week carrying its own subtotal,
        plus a grand total for the whole range.
        """
        tz = pytz.timezone(employee.tz or self.env.user.tz or 'UTC')
        no_overtime = self.env['hr.workday.overtime']
        rows = [
            self._build_row(results[employee.id, day], tz,
                            overtime.get((employee.id, day), no_overtime))
            for day in date_range(date_from, date_to)
        ]

        def close(bucket):
            return {
                'rows': bucket,
                'totals': self._totals(bucket),
                'date_from': bucket[0]['date'],
                'date_to': bucket[-1]['date'],
            }

        weeks, current, current_key = [], [], None
        for row in rows:
            key = row['date'].isocalendar()[:2]
            if key != current_key and current:
                weeks.append(close(current))
                current = []
            current_key = key
            current.append(row)
        if current:
            weeks.append(close(current))

        return {
            'employee': employee,
            'weeks': weeks,
            'totals': self._totals(rows),
        }

    # ------------------------------------------------------------------
    # Report entry point
    # ------------------------------------------------------------------
    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        employees = self.env['hr.employee'].browse(data.get('employee_ids', []))
        date_from = fields.Date.to_date(data.get('date_from'))
        date_to = fields.Date.to_date(data.get('date_to'))
        # Stored snapshot for processed days, dry run for the rest: nothing
        # is written, and a locked day prints what payroll consumed.
        results = self.env['hr.workday']._engine_results(employees, date_from, date_to)
        overtime = self.env['hr.workday.overtime'].search([
            ('employee_id', 'in', employees.ids),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            ('state', '=', 'approved'),
        ]).grouped(lambda o: (o.employee_id.id, o.date))
        blocks = [self._build_employee_block(emp, date_from, date_to, results, overtime)
                  for emp in employees]

        # Legend: only the absence types that actually show up in the range.
        legend = self.env['hr.absence.type'].browse()
        for block in blocks:
            for week in block['weeks']:
                for row in week['rows']:
                    legend |= row['absence_types']

        company = self.env.company
        return {
            'doc_model': 'hr.employee',
            'docs': employees,
            'date_from': date_from,
            'date_to': date_to,
            # Naive UTC — the 'datetime' widget localizes it itself.
            'printed_on': fields.Datetime.now(),
            'blocks': blocks,
            'legend': legend.sorted('code'),
            'company': company,
            # Brand colors, with the Odoo defaults as fallback.
            'brand': company.primary_color or '#875A7B',
            'brand2': company.secondary_color or '#5B4757',
        }
