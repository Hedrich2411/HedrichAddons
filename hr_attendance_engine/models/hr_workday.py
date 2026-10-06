# -*- coding: utf-8 -*-
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain
from odoo.tools.misc import format_date


DAY_TYPE_SELECTION = [
    ('working', 'Working'),
    ('rest', 'Rest day'),
    ('leave', 'Leave'),
    ('holiday', 'Holiday'),
    ('no_schedule', 'No schedule'),
]

STATE_SELECTION = [
    ('draft', 'Draft'),
    ('processed', 'Processed'),
    ('locked', 'Locked'),
]

# Margin (hours) added before planned entry and after planned exit when
# pulling attendances. Shifts closer than twice this split the gap at its
# midpoint instead, so a punch never lands in two workdays.
ATTENDANCE_WINDOW_MARGIN_HOURS = 4

# Minimum overlap (minutes) between a gap in the punches and the planned
# break for that gap to be taken as the break. Below it the break counts as
# unpunched and the planned one is subtracted instead.
BREAK_MIN_OVERLAP_MINUTES = 5

MANAGER_GROUP = 'hr_attendance.group_hr_attendance_manager'


# ----------------------------------------------------------------------
# Time spans: lists of disjoint (start, end) naive UTC datetimes
# ----------------------------------------------------------------------
def date_range(date_from, date_to):
    """Every date from date_from to date_to, both included."""
    return [date_from + timedelta(days=i)
            for i in range((date_to - date_from).days + 1)]


def to_hours(td):
    return td.total_seconds() / 3600.0


def measure(spans):
    return sum(to_hours(end - start) for start, end in spans)


def clip(spans, start, end):
    """The part of each span that falls inside [start, end]."""
    out = []
    for s, e in spans:
        s, e = max(s, start), min(e, end)
        if e > s:
            out.append((s, e))
    return out


def subtract(spans, holes):
    for hole_start, hole_end in holes:
        out = []
        for s, e in spans:
            if hole_end <= s or hole_start >= e:
                out.append((s, e))
                continue
            if s < hole_start:
                out.append((s, hole_start))
            if hole_end < e:
                out.append((hole_end, e))
        spans = out
    return spans


def check_manager(env, message):
    if not (env.su or env.user.has_group(MANAGER_GROUP)):
        raise AccessError(message)


class HrWorkday(models.Model):
    _name = 'hr.workday'
    _description = 'Workday snapshot per (employee, date)'
    _order = 'date desc, employee_id'
    _rec_names_search = ['employee_id.name', 'date']

    # --- Identity ---
    employee_id = fields.Many2one(
        'hr.employee', required=True, ondelete='cascade', index=True,
    )
    date = fields.Date(
        required=True, index=True,
        help="Calendar date the workday belongs to. For shifts that cross "
             "midnight, this is the entry date.",
    )
    company_id = fields.Many2one(
        'res.company', related='employee_id.company_id',
        store=True, index=True,
    )
    state = fields.Selection(
        STATE_SELECTION, required=True, default='draft', copy=False,
    )
    day_type = fields.Selection(
        DAY_TYPE_SELECTION, default='no_schedule',
        help="Set by the engine when processing the day.",
    )

    # --- Schedule snapshot (frozen at process time) ---
    schedule_line_id = fields.Many2one(
        'hr.schedule.line', ondelete='set null', readonly=True,
        help="The schedule line that applied on this date.",
    )
    planned_entry_dt = fields.Datetime(string='Planned entry')
    planned_exit_dt = fields.Datetime(string='Planned exit')
    planned_break_start_dt = fields.Datetime(string='Planned break start')
    planned_break_end_dt = fields.Datetime(string='Planned break end')
    crosses_midnight = fields.Boolean()
    window_start_dt = fields.Datetime(
        string='Window start', readonly=True,
        help="Check-ins from here up to the window end belong to this "
             "workday. Neighbouring workdays never share a moment.",
    )
    window_end_dt = fields.Datetime(string='Window end', readonly=True)

    # --- Real timestamps (from punches, after noise filtering) ---
    real_entry_dt = fields.Datetime(string='Real entry')
    real_exit_dt = fields.Datetime(string='Real exit')
    real_break_start_dt = fields.Datetime(string='Real break start')
    real_break_end_dt = fields.Datetime(string='Real break end')

    # --- Computed metrics (set by engine, stored as snapshots).
    # All stored as hours (Float). UI renders with widget="float_time"
    # so the user sees "HH:MM".
    expected_hours = fields.Float(string='Expected hours')
    worked_hours = fields.Float(string='Worked hours')
    late_hours = fields.Float(string='Late hours')
    early_leave_hours = fields.Float(string='Early leave hours')
    is_absent = fields.Boolean(
        help="Scheduled to work but no attendance was found.",
    )

    # --- Absence linking (one full-day leave OR several partial-day
    #     absences on the same day) ---
    absence_ids = fields.Many2many(
        'hr.absence', 'hr_workday_absence_rel',
        'workday_id', 'absence_id',
        string='Absences', readonly=True, check_company=True,
        help="Approved absences covering this date. Either a single "
             "full-day leave or one or more partial-day permissions "
             "(hourly or half-day) that sum into leave_hours.",
    )
    leave_hours = fields.Float(
        string='Leave hours', readonly=True,
        help="Hours of partial-day absence(s) subtracted from the "
             "expected working time. 0 when the day is a full leave or no "
             "absence applies.",
    )
    holiday_worked_hours = fields.Float(
        string='Worked on holiday/rest', readonly=True,
        help="Hours actually worked on a public holiday or on a weekly "
             "rest day. Peruvian law pays these with a 100% surcharge "
             "unless a substitute rest day is granted, so payroll needs "
             "them apart from the regular worked hours.",
    )
    unauthorized_absence_hours = fields.Float(
        string='Unauthorized absence', readonly=True,
        help="Hours the worker was missing during the planned window with "
             "no approved absence covering them (excess break, late return "
             "from a permit, etc.). Already nets out tolerance forgiveness. "
             "Use as the discount input for payroll.",
    )
    incongruency_message = fields.Text(
        string='Incongruency',
        readonly=True,
        help="Set by the engine when the punches received don't match the "
             "expected structure (e.g., 4 punches expected with break, got "
             "3). Investigate and clean the source data, then reprocess.",
    )

    # --- Additional hours (overtime / compensatory) ---
    overtime_ids = fields.One2many(
        'hr.workday.overtime', 'workday_id',
        string='Additional hours',
    )
    overtime_paid_hours = fields.Float(
        compute='_compute_overtime_totals', store=True,
        string='Paid overtime',
        help="Sum of approved paid overtime entries for this workday.",
    )
    overtime_compensatory_hours = fields.Float(
        compute='_compute_overtime_totals', store=True,
        string='Compensatory overtime',
        help="Sum of approved compensatory overtime entries for this "
             "workday. Adds to the employee's compensatory balance.",
    )

    @api.depends('overtime_ids.state', 'overtime_ids.type',
                 'overtime_ids.duration_hours')
    def _compute_overtime_totals(self):
        for rec in self:
            approved = rec.overtime_ids.filtered(lambda o: o.state == 'approved')
            rec.overtime_paid_hours = sum(
                approved.filtered(lambda o: o.type == 'paid').mapped('duration_hours'))
            rec.overtime_compensatory_hours = sum(
                approved.filtered(lambda o: o.type == 'compensatory').mapped('duration_hours'))

    # --- Audit ---
    attendance_ids = fields.Many2many(
        'hr.attendance', 'hr_workday_attendance_rel',
        'workday_id', 'attendance_id',
        string='Used attendances', check_company=True,
        help="Attendances the engine considered to compute this workday.",
    )
    notes = fields.Text()

    # --- Attendance review ("tareo") ---
    is_reviewed = fields.Boolean(
        string='Reviewed',
        help="A supervisor checked the punches of this day in the "
             "attendance review before the engine processed it.",
    )
    review_user_id = fields.Many2one('res.users', string='Reviewed by',
                                     readonly=True)
    reviewed_on = fields.Datetime(readonly=True)

    _employee_date_unique = models.Constraint(
        'UNIQUE(employee_id, date)',
        'There can only be one workday per (employee, date).',
    )

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------
    @api.depends('employee_id', 'date')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = (
                f"{rec.employee_id.name or ''} — {rec.date or ''}"
            ).strip(' —')

    def _engine_schedule_label(self, tz, sep=' / '):
        """Planned shift in local time, e.g. "08:00-13:00 / 14:00-17:00".
        Read from the snapshot, so a locked day keeps the hours it was
        computed with even if the template changed since.
        """
        self.ensure_one()
        if not self.planned_entry_dt:
            if self.schedule_line_id.is_rest_day:
                return _("Rest day")
            return _("No schedule")

        def hhmm(dt):
            return pytz.utc.localize(dt).astimezone(tz).strftime('%H:%M')

        entry, exit_ = hhmm(self.planned_entry_dt), hhmm(self.planned_exit_dt)
        if self.planned_break_start_dt:
            return (f"{entry}-{hhmm(self.planned_break_start_dt)}{sep}"
                    f"{hhmm(self.planned_break_end_dt)}-{exit_}")
        return f"{entry}-{exit_}"

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------
    def action_process(self):
        """Re-run the engine on the selected workdays."""
        for rec in self:
            if rec.state == 'locked':
                raise UserError(_(
                    "Workday %s is locked. Unlock it first to reprocess.",
                    rec.display_name,
                ))
        self._engine_process()
        return True

    def action_lock(self):
        for rec in self:
            if rec.state == 'draft':
                raise UserError(_(
                    "Process %s before locking.", rec.display_name,
                ))
        self.write({'state': 'locked'})
        return True

    def action_unlock(self):
        self.write({'state': 'processed'})
        return True

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state == 'locked':
                raise UserError(_(
                    "Unlock %s before resetting.", rec.display_name,
                ))
        self.write({'state': 'draft'})
        return True

    # ------------------------------------------------------------------
    # Bulk creation (Generate Workdays wizard)
    # ------------------------------------------------------------------
    @api.model
    def _generate(self, employees, date_from, date_to):
        """Create missing workdays for the (employees x date range), then
        process every workday that isn't locked.

        Returns a summary dict::

            {
                'created':  int,  # workdays just created
                'processed': int, # workdays (re)processed (created + existing draft/processed)
                'skipped_locked': int,  # workdays left untouched because locked
                'workdays': recordset,  # all affected
            }
        """
        if date_from > date_to:
            raise UserError(_("'Date from' must be on or before 'Date to'."))

        dates = date_range(date_from, date_to)
        existing = self.search([
            ('employee_id', 'in', employees.ids),
            ('date', 'in', dates),
        ])
        existing_keys = {(w.employee_id.id, w.date) for w in existing}

        to_create = [
            {'employee_id': emp.id, 'date': d}
            for emp in employees
            for d in dates
            if (emp.id, d) not in existing_keys
        ]
        created = self.create(to_create) if to_create else self.browse()

        affected = existing | created
        processable = affected.filtered(lambda w: w.state != 'locked')
        processable._engine_process()

        return {
            'created': len(created),
            'processed': len(processable),
            'skipped_locked': len(affected) - len(processable),
            'workdays': affected,
        }

    # ------------------------------------------------------------------
    # Attendance review ("tareo") — consumed by the OWL grid
    # ------------------------------------------------------------------
    @api.model
    def review_grid(self, employee_ids, date_from, date_to):
        """Day-by-day state of each employee over the range. Creates
        nothing: the review happens *before* the workdays are generated.

        Returns::

            {'dates': [{'date', 'label', 'weekday', 'is_weekend'}, ...],
             'rows':  [{'id', 'name', 'cells': [...]}, ...]}
        """
        employees = self.env['hr.employee'].browse(employee_ids).exists()
        if not self.env.user.has_group('hr_attendance.group_hr_attendance_user'):
            # Officers only manage their own people, as on native attendances.
            employees = employees.filtered(
                lambda e: e.attendance_manager_id == self.env.user)
        date_from = fields.Date.to_date(date_from)
        date_to = fields.Date.to_date(date_to)
        dates = date_range(date_from, date_to)
        results = self._engine_results(employees, date_from, date_to)

        rows = []
        for employee in employees:
            tz = pytz.timezone(employee.tz or self.env.user.tz or 'UTC')
            rows.append({
                'id': employee.id,
                'name': employee.display_name,
                'cells': [results[employee.id, day]._review_cell(tz) for day in dates],
            })

        return {
            'dates': [{
                'date': fields.Date.to_string(day),
                'label': format_date(self.env, day, date_format='EEE d'),
                'weekday': day.weekday(),
                'is_weekend': day.weekday() >= 5,
            } for day in dates],
            'rows': rows,
        }

    def _review_cell(self, tz):
        """One cell of the review grid, from an engine result."""
        self.ensure_one()
        if self.incongruency_message:
            status = 'incongruent'
        elif self.is_absent:
            status = 'absent'
        elif self.day_type != 'working':
            status = self.day_type
        else:
            status = 'ok'

        def local(dt):
            return dt and pytz.utc.localize(dt).astimezone(tz).strftime('%H:%M')

        return {
            'date': fields.Date.to_string(self.date),
            'status': status,
            'day_type': self.day_type,
            'incongruency': self.incongruency_message or '',
            'schedule': self._engine_schedule_label(tz),
            'worked_hours': self.worked_hours,
            'punches': [{
                'id': a.id,
                'check_in': local(a.check_in),
                'check_out': local(a.check_out),
                'open': not a.check_out,
            } for a in self.attendance_ids.sorted('check_in')],
            'is_reviewed': self.is_reviewed,
            'is_locked': self.state == 'locked',
            'note': self.notes or '',
        }

    @api.model
    def review_mark(self, employee_ids, dates, note=None):
        """Flag (employee, date) pairs as reviewed.

        Holds the flag on a draft hr.workday rather than a model of its own:
        the uniqueness constraint, the company rules and the notes field are
        already there, and ``_generate`` reuses existing rows, so a reviewed
        draft simply gets processed later.
        """
        dates = [fields.Date.to_date(d) for d in dates]
        employees = self.env['hr.employee'].browse(employee_ids).exists()

        existing = self.search([
            ('employee_id', 'in', employees.ids),
            ('date', 'in', dates),
        ])
        by_key = {(w.employee_id.id, w.date): w for w in existing}

        stamp = {
            'is_reviewed': True,
            'review_user_id': self.env.uid,
            'reviewed_on': fields.Datetime.now(),
        }
        if note is not None:
            stamp['notes'] = note

        to_create = []
        for employee in employees:
            for day in dates:
                workday = by_key.get((employee.id, day))
                if workday:
                    if workday.state != 'locked':
                        workday.write(stamp)
                else:
                    to_create.append({
                        'employee_id': employee.id,
                        'date': day,
                        **stamp,
                    })
        if to_create:
            self.create(to_create)
        return True

    # ------------------------------------------------------------------
    # Engine
    # ------------------------------------------------------------------
    def _engine_process(self):
        """Run the engine on these workdays and store the result."""
        if not self:
            return
        dates = self.mapped('date')
        data = self._engine_load(self.employee_id, min(dates), max(dates))
        for rec in self:
            rec.write({**rec._compute_values(data), 'state': 'processed'})

    @api.model
    def _engine_results(self, employees, date_from, date_to):
        """``{(employee_id, date): workday}`` over the range. Writes nothing.

        A day the engine already processed gives its stored snapshot, the
        one payroll reads. Any other day gets an in-memory dry run on top of
        its draft row, if there is one, so review flags and notes still show.
        """
        stored = {(w.employee_id.id, w.date): w for w in self.search([
            ('employee_id', 'in', employees.ids),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
        ])}
        data = self._engine_load(employees, date_from, date_to)
        results = {}
        for employee in employees:
            for day in date_range(date_from, date_to):
                workday = stored.get((employee.id, day), self.browse())
                if workday.state not in ('processed', 'locked'):
                    workday = self.new(
                        {'employee_id': employee.id, 'date': day},
                        origin=workday or None,
                    )
                    workday.update(workday._compute_values(data))
                results[employee.id, day] = workday
        return results

    @api.model
    def _engine_load(self, employees, date_from, date_to):
        """Everything the engine reads for (employees x range), in a handful
        of queries. Schedules reach one day past each end because a day's
        window depends on its neighbours'; punches reach further to cover
        any timezone.
        """
        one_day = timedelta(days=1)
        absences = self.env['hr.absence'].search([
            ('employee_id', 'in', employees.ids),
            ('state', '=', 'approved'),
            ('date_from', '<=', date_to),
            ('date_to', '>=', date_from),
        ])
        holidays = self.env['hr.public.holiday'].search([
            ('date', '>=', date_from),
            ('date', '<=', date_to),
        ])
        attendances = self.env['hr.attendance'].search([
            ('employee_id', 'in', employees.ids),
            ('check_in', '>=', datetime.combine(date_from - 2 * one_day, time.min)),
            ('check_in', '<', datetime.combine(date_to + 3 * one_day, time.min)),
        ], order='check_in')
        return {
            'lines': self.env['hr.schedule.assignment']._lines(
                employees, date_from - one_day, date_to + one_day,
            ),
            'absences': absences.grouped('employee_id'),
            'holidays': {(h.company_id.id, h.date) for h in holidays},
            'attendances': attendances.grouped('employee_id'),
        }

    def _compute_values(self, data=None):
        """Classify the day, snapshot the planned shift, pair the punches
        and measure late / early leave / worked hours. Returns the values
        dict; writes nothing.
        """
        self.ensure_one()
        employee, day = self.employee_id, self.date
        data = data or self._engine_load(employee, day, day)
        tz = self._engine_tz()
        no_line = self.env['hr.schedule.line']
        one_day = timedelta(days=1)

        def planned_on(d):
            return self._engine_planned(
                d, data['lines'].get((employee.id, d), no_line), tz,
            )

        line = data['lines'].get((employee.id, day), no_line)
        planned = planned_on(day)
        window = self._engine_window(planned, planned_on(day - one_day),
                                     planned_on(day + one_day), tz)
        day_type, absences = self._engine_classify(
            line,
            data['absences'].get(employee, self.env['hr.absence']),
            data['holidays'],
        )
        attendances = data['attendances'].get(
            employee, self.env['hr.attendance'],
        ).filtered(lambda a: window[0] <= a.check_in < window[1])

        # Expected working time: the shift minus its break, minus whatever
        # partial-day permits free on a working day.
        base = []
        if planned['entry']:
            base = subtract(
                [(planned['entry'], planned['exit'])],
                [(planned['break_start'], planned['break_end'])]
                if planned['break_start'] else [],
            )
        permits = [self._engine_permit(a, line, planned, tz) for a in absences] \
            if day_type == 'working' else []
        segments = subtract(base, permits)

        m = self._engine_metrics(line, planned, segments, permits,
                                 attendances, day_type)
        return {
            'day_type': day_type,
            'absence_ids': [(6, 0, absences.ids)],
            'leave_hours': measure(base) - measure(segments),
            'schedule_line_id': line.id,
            'crosses_midnight': line.crosses_midnight,
            'planned_entry_dt': planned['entry'],
            'planned_exit_dt': planned['exit'],
            'planned_break_start_dt': planned['break_start'],
            'planned_break_end_dt': planned['break_end'],
            'window_start_dt': window[0],
            'window_end_dt': window[1],
            'real_entry_dt': m['real_entry'],
            'real_exit_dt': m['real_exit'],
            'real_break_start_dt': m['real_break_start'],
            'real_break_end_dt': m['real_break_end'],
            'expected_hours': measure(segments),
            'worked_hours': m['worked'],
            'late_hours': m['late'],
            'early_leave_hours': m['early_leave'],
            'holiday_worked_hours': m['holiday_worked'],
            'unauthorized_absence_hours': m['unauthorized'],
            'is_absent': m['is_absent'],
            'incongruency_message': ' '.join(m['incongruency']) or False,
            'attendance_ids': [(6, 0, attendances.ids)],
        }

    # --- Engine: helpers -----------------------------------------------
    def _engine_tz(self):
        return self.employee_id.tz or self.env.user.tz or 'UTC'

    @api.model
    def _engine_at(self, day, line, hour, tz):
        """UTC moment of a local ``hour`` inside the shift starting on
        ``day``: on a night shift, hours before the entry hour belong to the
        next morning. Used for exits, breaks and hourly permits alike.
        """
        if line.crosses_midnight and hour < line.planned_entry:
            day += timedelta(days=1)
        return self._hour_to_utc(day, hour, tz)

    @api.model
    def _engine_planned(self, day, line, tz):
        """The line's planned shift on ``day`` as naive UTC datetimes."""
        planned = dict.fromkeys(('entry', 'exit', 'break_start', 'break_end'), False)
        if line and not line.is_rest_day:
            planned['entry'] = self._engine_at(day, line, line.planned_entry, tz)
            planned['exit'] = self._engine_at(day, line, line.planned_exit, tz)
            if line.has_break:
                planned['break_start'] = self._engine_at(day, line, line.break_start, tz)
                planned['break_end'] = self._engine_at(day, line, line.break_end, tz)
        return planned

    def _engine_window(self, planned, prev, nxt, tz):
        """``(start, end)`` of the check-ins that belong to this workday.

        A planned shift reaches the margin past each end; an unplanned day
        covers its local calendar day. Facing a planned neighbour, two
        shifts split the gap at its midpoint and an unplanned day yields the
        neighbour its margin, so consecutive windows never overlap.
        """
        margin = timedelta(hours=ATTENDANCE_WINDOW_MARGIN_HOURS)
        entry, exit_ = planned['entry'], planned['exit']
        if entry:
            start, end = entry - margin, exit_ + margin
        else:
            start = self._hour_to_utc(self.date, 0.0, tz)
            end = self._hour_to_utc(self.date + timedelta(days=1), 0.0, tz)
        if prev['exit']:
            start = max(start, prev['exit'] + (entry - prev['exit']) / 2
                        if entry else prev['exit'] + margin)
        if nxt['entry']:
            end = min(end, exit_ + (nxt['entry'] - exit_) / 2
                      if entry else nxt['entry'] - margin)
        return start, end

    def _engine_classify(self, line, absences, holidays):
        """``(day_type, absences that apply)``, in this order:

        1. An approved full-day absence → leave (only that one is kept;
           vacations are calendar days, so this wins over a holiday).
        2. A public holiday, global or of the company → holiday.
        3. No schedule line → no_schedule; a rest line → rest.
        4. Otherwise working, with the partial-day permits (hourly or
           half-day) that cut into it.
        """
        absences = absences.filtered(lambda a: a.date_from <= self.date <= a.date_to)
        full = absences.filtered(lambda a: a.unit == 'day')
        if full:
            return 'leave', full[:1]
        none = absences.browse()
        if {(False, self.date), (self.company_id.id, self.date)} & holidays:
            return 'holiday', none
        if not line:
            return 'no_schedule', none
        if line.is_rest_day:
            return 'rest', none
        return 'working', absences

    def _engine_permit(self, absence, line, planned, tz):
        """UTC span a partial-day absence frees on this shift."""
        if absence.unit == 'hour':
            return (self._engine_at(self.date, line, absence.hour_from, tz),
                    self._engine_at(self.date, line, absence.hour_to, tz))
        # Half day: the half before or after the break, split at the middle
        # of the shift when it has none.
        middle = planned['entry'] + (planned['exit'] - planned['entry']) / 2
        if absence.half_day_period == 'pm':
            return planned['break_end'] or middle, planned['exit']
        return planned['entry'], planned['break_start'] or middle

    def _engine_tolerance(self, delta, tolerance_minutes):
        """``(counted, forgiven)`` hours of a ``delta``-hour delay."""
        if delta <= 0:
            return 0.0, 0.0
        tolerance = tolerance_minutes / 60.0
        if delta <= tolerance:
            return 0.0, delta
        if self.company_id.workday_tolerance_loss_mode == 'strict':
            return delta, 0.0
        return delta - tolerance, tolerance

    def _engine_metrics(self, line, planned, segments, permits, attendances, day_type):
        """Real timestamps plus worked / late / early leave / unauthorized
        hours and the incongruencies found. ``segments`` is the expected
        working time: the shift minus break and permits.
        """
        out = {'real_entry': False, 'real_exit': False,
               'real_break_start': False, 'real_break_end': False,
               'worked': 0.0, 'late': 0.0, 'early_leave': 0.0,
               'unauthorized': 0.0, 'holiday_worked': 0.0,
               'is_absent': False, 'incongruency': []}
        closed = [(a.check_in, a.check_out) for a in attendances if a.check_out]
        if attendances:
            out['real_entry'] = attendances[0].check_in
            # Forgot to punch out: the exit is unknown, not early.
            out['real_exit'] = attendances[-1].check_out
            if not attendances[-1].check_out:
                out['incongruency'].append(_("Last attendance has no check-out (open punch)."))

        if day_type in ('holiday', 'rest'):
            # Nothing was planned, so there is nothing to clip to: every
            # punched hour counts, kept apart from worked_hours because
            # payroll surcharges it.
            out['holiday_worked'] = measure(closed)
            return out
        if day_type != 'working':
            if attendances:
                out['incongruency'].append(
                    _("Attendances found during a full-day absence.")
                    if day_type == 'leave'
                    else _("Attendances found on a day without schedule."))
            return out

        expected = measure(segments)
        if not attendances:
            out['is_absent'] = expected > 0
            out['unauthorized'] = expected
            return out

        # One record per stretch of expected work: the break and every
        # mid-shift permit add one.
        if len(attendances) != len(segments):
            out['incongruency'].insert(0, _(
                "Expected %(expected)s attendance record(s) "
                "(%(punches)s punches) for this day, got %(got)s.",
                expected=len(segments), punches=len(segments) * 2,
                got=len(attendances),
            ))

        # The break is the gap between records that overlaps the planned
        # one the most. Its length is what counts, not its exact position.
        planned_break = planned['break_start'] and (planned['break_start'], planned['break_end'])
        real_break = None
        if planned_break:
            gaps = [(a.check_out, b.check_in)
                    for a, b in zip(attendances, attendances[1:]) if a.check_out]
            best = max(gaps, key=lambda g: measure(clip([g], *planned_break)), default=None)
            if best and measure(clip([best], *planned_break)) * 60 >= BREAK_MIN_OVERLAP_MINUTES:
                real_break = best
                out['real_break_start'], out['real_break_end'] = best

        # Worked: the punched time inside the shift, never the time a permit
        # already frees, and minus the planned break when none was punched.
        # Early arrivals and late stays are not counted: overtime is
        # registered apart, in hr.workday.overtime.
        worked = subtract(clip(closed, planned['entry'], planned['exit']), permits)
        if planned_break and not real_break:
            worked = subtract(worked, [planned_break])
        out['worked'] = measure(worked)

        late_forgiven = early_forgiven = 0.0
        if segments:
            # The first and last stretch already account for permits at
            # either end of the shift.
            out['late'], late_forgiven = self._engine_tolerance(
                to_hours(out['real_entry'] - segments[0][0]),
                line.entry_tolerance_minutes,
            )
            if out['real_exit']:
                out['early_leave'], early_forgiven = self._engine_tolerance(
                    to_hours(segments[-1][1] - out['real_exit']),
                    line.exit_tolerance_minutes,
                )

        # Time missing from worked that isn't already late / early leave or
        # forgiven by the tolerance: excess break, late return from a
        # permit, an unpunched gap...
        out['unauthorized'] = max(
            0.0,
            expected - out['worked'] - out['late'] - out['early_leave']
            - late_forgiven - early_forgiven,
        )
        return out

    @staticmethod
    def _hour_to_utc(day, hour_float, tz_name):
        """(date, local hour as float, tz) → naive UTC datetime. 24.0 is
        the next midnight."""
        local = datetime.combine(day, time()) + timedelta(minutes=round(hour_float * 60))
        return pytz.timezone(tz_name).localize(local).astimezone(pytz.utc).replace(tzinfo=None)

    # ------------------------------------------------------------------
    # Source changes
    # ------------------------------------------------------------------
    @api.model
    def _engine_touched_days(self, pairs):
        """Non-draft workdays at these (employee_id, date) pairs."""
        pairs = set(pairs)
        if not pairs:
            return self.browse()
        workdays = self.search([
            ('employee_id', 'in', list({e for e, _d in pairs})),
            ('date', 'in', list({d for _e, d in pairs})),
            ('state', '!=', 'draft'),
        ])
        return workdays.filtered(lambda w: (w.employee_id.id, w.date) in pairs)

    @api.model
    def _engine_touched_moments(self, pairs):
        """Non-draft workdays whose window holds these (employee, check_in)."""
        domains = [
            [('employee_id', '=', employee.id),
             ('window_start_dt', '<=', check_in),
             ('window_end_dt', '>', check_in)]
            for employee, check_in in pairs if employee and check_in
        ]
        if not domains:
            return self.browse()
        return self.search(Domain.OR(domains) & Domain('state', '!=', 'draft'))

    def _engine_sources_changed(self):
        """The data the engine read for these workdays changed.

        A locked workday refuses it: payroll already consumed its snapshot.
        A processed one goes back to draft so nothing reads stale numbers;
        Generate Workdays processes it again.
        """
        locked = self.filtered(lambda w: w.state == 'locked')
        if locked:
            raise UserError(_(
                "%s is a locked workday. Unlock it before changing the data "
                "behind it, then process it again.",
                locked[0].display_name,
            ))
        self.filtered(lambda w: w.state == 'processed').write({'state': 'draft'})

    # ------------------------------------------------------------------
    # CRUD and lock guard
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        workdays = super().create(vals_list)
        # Additional hours registered before their workday existed.
        self.env['hr.workday.overtime'].search([
            ('workday_id', '=', False),
            ('employee_id', 'in', workdays.employee_id.ids),
            ('date', 'in', workdays.mapped('date')),
        ])._link_workday()
        return workdays

    def write(self, vals):
        if any(rec.state == 'locked' for rec in self):
            forbidden = set(vals) - {'state', 'notes'}
            if forbidden:
                raise UserError(_(
                    "Cannot edit locked workdays. Forbidden fields: %s",
                    ', '.join(sorted(forbidden)),
                ))
            if vals.get('state', 'locked') != 'locked':
                check_manager(self.env, _(
                    "Only attendance administrators can unlock workdays."))
        if vals.get('state') == 'locked':
            now = fields.Datetime.now()
            in_progress = self.filtered(lambda w: w.window_end_dt and w.window_end_dt > now)
            if in_progress:
                # Its punches are still coming in: locking it would make the
                # kiosk refuse the employee's next check-in or check-out.
                raise UserError(_(
                    "%s is still in progress. Lock it once its last punch "
                    "is in.", in_progress[0].display_name,
                ))
        return super().write(vals)

    def unlink(self):
        if any(rec.state == 'locked' for rec in self):
            raise UserError(_("Locked workdays cannot be deleted. Unlock them first."))
        return super().unlink()
