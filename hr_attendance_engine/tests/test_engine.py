# -*- coding: utf-8 -*-
from datetime import date, datetime, time, timedelta

import pytz

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

LIMA = pytz.timezone('America/Lima')
MONDAY = date(2026, 3, 2)
ONE_DAY = timedelta(days=1)


def utc(day, hhmm):
    """Local Lima "HH:MM" on ``day`` → naive UTC."""
    hour, minute = map(int, hhmm.split(':'))
    local = LIMA.localize(datetime.combine(day, time(hour, minute)))
    return local.astimezone(pytz.utc).replace(tzinfo=None)


@tagged('post_install', '-at_install')
class TestEngine(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.workday_tolerance_loss_mode = 'lenient'
        cls.employee = cls.env['hr.employee'].create({
            'name': 'Rosa Quispe', 'tz': 'America/Lima',
        })
        cls.office = cls._schedule('Office 08-17', 8, 17, brk=(13, 14), tol=(10, 5))
        cls.night = cls._schedule('Night 22-06', 22, 6, brk=(2, 2.5))
        cls.rest = cls.env['hr.schedule'].create({
            'name': 'Rest', 'line_ids': [
                (0, 0, {'day_of_week': str(d), 'is_rest_day': True}) for d in range(7)
            ],
        })
        cls.permit_type = cls.env.ref('hr_attendance_engine.absence_type_permission_paid')

    @classmethod
    def _schedule(cls, name, entry, exit_, brk=None, tol=(0, 0)):
        line = {
            'planned_entry': entry, 'planned_exit': exit_,
            'entry_tolerance_minutes': tol[0], 'exit_tolerance_minutes': tol[1],
        }
        if brk:
            line.update(has_break=True, break_start=brk[0], break_end=brk[1])
        return cls.env['hr.schedule'].create({
            'name': name,
            'line_ids': [(0, 0, {**line, 'day_of_week': str(d)}) for d in range(7)],
        })

    # --- helpers --------------------------------------------------------
    def assign(self, schedule, day=MONDAY, employee=None):
        self.env['hr.schedule.assignment'].create({
            'employee_id': (employee or self.employee).id,
            'date': day, 'schedule_id': schedule.id,
        })

    def punch(self, *spans, day=MONDAY, employee=None):
        """("08:00", "13:00") pairs; an end before its start is the next day,
        a missing end leaves the punch open."""
        records = self.env['hr.attendance']
        for start, end in spans:
            check_in = utc(day, start)
            vals = {'employee_id': (employee or self.employee).id, 'check_in': check_in}
            if end:
                check_out = utc(day, end)
                vals['check_out'] = check_out if check_out > check_in else utc(day + ONE_DAY, end)
            records |= records.create(vals)
        return records

    def absence(self, unit='hour', day=MONDAY, day_to=None, type_id=None, **vals):
        return self.env['hr.absence'].create({
            'employee_id': self.employee.id,
            'type_id': (type_id or self.permit_type).id,
            'unit': unit, 'date_from': day, 'date_to': day_to or day,
            'state': 'approved', **vals,
        })

    def process(self, day=MONDAY, employee=None):
        return self.env['hr.workday']._generate(employee or self.employee, day, day)['workdays']

    # --- permits ----------------------------------------------------------
    def test_permit_at_start_is_not_deducted_twice(self):
        self.assign(self.office)
        self.absence(hour_from=8, hour_to=10)
        self.punch(('10:00', '13:00'), ('14:00', '17:00'))
        wd = self.process()
        self.assertAlmostEqual(wd.expected_hours, 6)
        self.assertAlmostEqual(wd.worked_hours, 6)
        self.assertAlmostEqual(wd.leave_hours, 2)
        self.assertEqual((wd.late_hours, wd.unauthorized_absence_hours), (0, 0))
        self.assertFalse(wd.incongruency_message)

    def test_permit_at_end_is_not_deducted_twice(self):
        self.assign(self.office)
        self.absence(hour_from=15, hour_to=17)
        self.punch(('08:00', '13:00'), ('14:00', '15:00'))
        wd = self.process()
        self.assertAlmostEqual(wd.worked_hours, 6)
        self.assertEqual((wd.early_leave_hours, wd.unauthorized_absence_hours), (0, 0))

    def test_mid_shift_permit_expects_an_extra_record(self):
        self.assign(self.office)
        self.absence(hour_from=10, hour_to=12)
        self.punch(('08:00', '10:00'), ('12:00', '13:00'), ('14:00', '17:00'))
        wd = self.process()
        self.assertFalse(wd.incongruency_message)
        self.assertAlmostEqual(wd.worked_hours, 6)
        self.assertEqual(wd.real_break_start_dt, utc(MONDAY, '13:00'))
        self.assertAlmostEqual(wd.unauthorized_absence_hours, 0)

    def test_permit_overlapping_break_only_frees_working_time(self):
        self.assign(self.office)
        self.absence(hour_from=12, hour_to=15)
        self.punch(('08:00', '12:00'), ('15:00', '17:00'))
        wd = self.process()
        self.assertAlmostEqual(wd.leave_hours, 2)  # 12-13 and 14-15
        self.assertAlmostEqual(wd.expected_hours, 6)
        self.assertAlmostEqual(wd.unauthorized_absence_hours, 0)

    def test_unpunched_gap_is_unauthorized(self):
        self.assign(self.office)
        self.punch(('08:00', '10:00'), ('12:00', '13:00'), ('14:00', '17:00'))
        wd = self.process()
        self.assertTrue(wd.incongruency_message)
        self.assertAlmostEqual(wd.worked_hours, 6)
        self.assertAlmostEqual(wd.unauthorized_absence_hours, 2)

    def test_half_day_morning(self):
        self.assign(self.office)
        self.absence(unit='half_day', half_day_period='am')
        self.punch(('14:00', '17:00'))
        wd = self.process()
        self.assertEqual(wd.day_type, 'working')
        self.assertAlmostEqual(wd.expected_hours, 3)
        self.assertAlmostEqual(wd.worked_hours, 3)
        self.assertEqual((wd.late_hours, wd.unauthorized_absence_hours), (0, 0))
        self.assertFalse(wd.incongruency_message)

    def test_hourly_permit_on_holiday_keeps_the_holiday(self):
        self.assign(self.office)
        self.env['hr.public.holiday'].create({'name': 'Feriado', 'date': MONDAY})
        self.absence(hour_from=8, hour_to=10)
        self.assertEqual(self.process().day_type, 'holiday')

    # --- lateness, breaks, punches -------------------------------------------
    def test_tolerance_lenient_and_strict(self):
        self.assign(self.office)
        self.punch(('08:20', '13:00'), ('14:00', '17:00'))
        wd = self.process()
        self.assertAlmostEqual(wd.late_hours, 10 / 60)
        self.assertAlmostEqual(wd.unauthorized_absence_hours, 0)

        self.env.company.workday_tolerance_loss_mode = 'strict'
        wd.action_process()
        self.assertAlmostEqual(wd.late_hours, 20 / 60)
        self.assertAlmostEqual(wd.unauthorized_absence_hours, 0)

    def test_excess_break_is_unauthorized(self):
        self.assign(self.office)
        self.punch(('08:00', '13:00'), ('14:30', '17:00'))
        wd = self.process()
        self.assertAlmostEqual(wd.worked_hours, 7.5)
        self.assertAlmostEqual(wd.unauthorized_absence_hours, 0.5)

    def test_no_punch_is_absent(self):
        self.assign(self.office)
        wd = self.process()
        self.assertTrue(wd.is_absent)
        self.assertAlmostEqual(wd.unauthorized_absence_hours, 8)

    def test_open_punch_is_not_early_leave(self):
        self.assign(self.office)
        self.punch(('08:00', '13:00'), ('14:00', None))
        wd = self.process()
        self.assertFalse(wd.real_exit_dt)
        self.assertEqual(wd.early_leave_hours, 0)
        self.assertTrue(wd.incongruency_message)

    def test_punches_without_schedule_are_flagged(self):
        self.punch(('08:00', '17:00'))
        wd = self.process()
        self.assertEqual(wd.day_type, 'no_schedule')
        self.assertTrue(wd.incongruency_message)

    # --- windows ------------------------------------------------------------
    def test_night_shift_tail_does_not_count_on_next_holiday(self):
        self.assign(self.night)
        self.assign(self.rest, day=MONDAY + ONE_DAY)
        self.env['hr.public.holiday'].create({'name': 'Feriado', 'date': MONDAY + ONE_DAY})
        self.punch(('22:00', '02:00'))
        self.punch(('02:30', '06:00'), day=MONDAY + ONE_DAY)
        night, holiday = self.env['hr.workday']._generate(
            self.employee, MONDAY, MONDAY + ONE_DAY)['workdays'].sorted('date')
        self.assertAlmostEqual(night.worked_hours, 7.5)
        self.assertFalse(night.incongruency_message)
        self.assertEqual(holiday.day_type, 'holiday')
        self.assertEqual(holiday.holiday_worked_hours, 0)

    def test_close_shifts_never_share_a_punch(self):
        self.assign(self._schedule('Late 14-22', 14, 22))
        self.assign(self._schedule('Early 02-10', 2, 10), day=MONDAY + ONE_DAY)
        self.punch(('14:00', '22:00'))
        early = self.punch(('01:50', '10:00'), day=MONDAY + ONE_DAY)
        late, nxt = self.env['hr.workday']._generate(
            self.employee, MONDAY, MONDAY + ONE_DAY)['workdays'].sorted('date')
        self.assertNotIn(early, late.attendance_ids)
        self.assertIn(early, nxt.attendance_ids)

    # --- overtime -----------------------------------------------------------
    def test_overtime_registered_before_workday_is_linked(self):
        self.assign(self.office)
        overtime = self.env['hr.workday.overtime'].create({
            'employee_id': self.employee.id, 'date': MONDAY,
            'type': 'paid', 'hour_from': 17, 'hour_to': 19,
        })
        overtime.action_approve()
        wd = self.process()
        self.assertEqual(overtime.workday_id, wd)
        self.assertAlmostEqual(wd.overtime_paid_hours, 2)

    # --- lock & invalidation -------------------------------------------------
    def test_locked_workday_guards_its_sources(self):
        self.assign(self.office)
        punches = self.punch(('08:00', '13:00'), ('14:00', '17:00'))
        wd = self.process()
        wd.action_lock()
        with self.assertRaises(UserError):
            punches[0].check_out = utc(MONDAY, '12:00')
        with self.assertRaises(UserError):
            self.punch(('18:00', '19:00'))
        with self.assertRaises(UserError):
            self.absence(hour_from=8, hour_to=9)
        with self.assertRaises(UserError):
            wd.unlink()

    def test_only_managers_unlock(self):
        officer = new_test_user(self.env, login='att_officer', groups=(
            'base.group_user,hr_attendance.group_hr_attendance_officer'))
        self.employee.attendance_manager_id = officer
        self.assign(self.office)
        wd = self.process()
        wd.action_lock()
        with self.assertRaises(AccessError):
            wd.with_user(officer).action_unlock()
        wd.action_unlock()
        self.assertEqual(wd.state, 'processed')

    def test_source_change_sends_processed_back_to_draft(self):
        self.assign(self.office)
        punches = self.punch(('08:00', '13:00'), ('14:00', '17:00'))
        wd = self.process()
        punches[1].check_out = utc(MONDAY, '16:00')
        self.assertEqual(wd.state, 'draft')
        self.process()
        self.absence(hour_from=16, hour_to=17)
        self.assertEqual(wd.state, 'draft')

    def test_locked_report_reads_the_snapshot(self):
        self.assign(self.office)
        wd = self.process()
        wd.action_lock()
        self.office.line_ids.write({'planned_exit': 18})
        result = self.env['hr.workday']._engine_results(self.employee, MONDAY, MONDAY)
        self.assertEqual(result[self.employee.id, MONDAY], wd)
        self.assertAlmostEqual(wd.expected_hours, 8)

    # --- approvals & vacations ------------------------------------------------
    def test_officer_cannot_approve_own_absence(self):
        officer = new_test_user(self.env, login='att_officer2', groups=(
            'base.group_user,hr_attendance.group_hr_attendance_user'))
        self.employee.write({'user_id': officer.id, 'attendance_manager_id': officer.id})
        absence = self.absence(hour_from=8, hour_to=9, state='confirmed')
        with self.assertRaises(AccessError):
            absence.with_user(officer).action_approve()

    def test_vacation_taken_the_year_after_it_was_earned(self):
        period = self.env['hr.vacation.period'].create({
            'employee_id': self.employee.id,
            'cycle_start': date(2025, 1, 1), 'cycle_end': date(2025, 12, 31),
            'expiry_date': date(2026, 12, 31), 'days_earned': 30,
        })
        vacation = self.absence(
            unit='day', day=MONDAY, day_to=MONDAY + timedelta(days=29), state='confirmed',
            type_id=self.env.ref('hr_attendance_engine.absence_type_vacation'),
        )
        vacation.action_approve()
        self.assertEqual(vacation.vacation_period_id, period)
        self.assertEqual(period.state, 'exhausted')

    def test_absence_hours_follow_the_schedule(self):
        self.assign(self.office)
        self.assign(self.rest, day=MONDAY + ONE_DAY)
        absence = self.absence(unit='day', day_to=MONDAY + ONE_DAY, state='draft')
        self.assertEqual(absence.duration_days, 2)
        self.assertAlmostEqual(absence.duration_hours, 8)  # 8h + rest day 0h

    def test_smart_button_counts(self):
        self.assign(self.office)
        self.assign(self.night, day=MONDAY + ONE_DAY)
        self.assign(self.office, day=MONDAY + 2 * ONE_DAY)
        self.absence(hour_from=8, hour_to=9)
        self.env['hr.vacation.period'].create({
            'employee_id': self.employee.id, 'cycle_start': date(2024, 1, 1),
            'cycle_end': date(2024, 12, 31), 'expiry_date': date(2099, 12, 31),
            'days_earned': 30, 'days_paid_cash': 10,
        })
        self.env['hr.vacation.period'].create({  # still being earned
            'employee_id': self.employee.id, 'cycle_start': date(2099, 1, 1),
            'cycle_end': date(2099, 12, 31), 'days_earned': 30,
        })
        self.assertEqual(self.employee.schedule_count, 2)
        self.assertEqual(self.employee.absence_count, 1)
        self.assertEqual(self.employee.vacation_days_left, 20)

    # --- the engine is in charge -------------------------------------------
    def test_native_overtime_never_runs(self):
        self.assign(self.office)
        self.punch(('06:00', '13:00'), ('14:00', '21:00'))  # 6h over the shift
        lines = self.env['hr.attendance.overtime.line'].search([('employee_id', '=', self.employee.id)])
        self.assertFalse(lines)
        self.assertEqual(self.employee.total_overtime, self.employee.compensatory_hours_balance)

    def test_day_in_progress_cannot_be_locked(self):
        today = datetime.now(LIMA).date()
        self.assign(self.office, day=today)
        wd = self.process(day=today)
        wd.window_end_dt = datetime.now() + timedelta(hours=1)
        with self.assertRaises(UserError):
            wd.action_lock()

    def test_monthly_hours_come_from_the_engine(self):
        today = datetime.now(LIMA).date()
        self.assign(self.office, day=today)
        # Early and late beyond the shift: the engine counts 8h, raw punches 11h.
        self.punch(('06:30', '13:00'), ('14:00', '18:30'), day=today)
        self.employee.invalidate_recordset(['hours_last_month'])
        self.assertAlmostEqual(self.employee.hours_last_month, 8)

    # --- multi-day permits & vacation periods ---------------------------------
    def period(self, start, earned=30, expiry=date(2099, 12, 31)):
        return self.env['hr.vacation.period'].create({
            'employee_id': self.employee.id, 'cycle_start': start,
            'cycle_end': start.replace(year=start.year + 1) - ONE_DAY,
            'expiry_date': expiry, 'days_earned': earned,
        })

    def test_lactation_hour_every_working_day(self):
        self.assign(self.office)
        self.assign(self.rest, day=MONDAY + ONE_DAY)
        self.assign(self.office, day=MONDAY + 2 * ONE_DAY)
        lactation = self.absence(
            day_to=MONDAY + 2 * ONE_DAY, hour_from=16, hour_to=17,
            type_id=self.env.ref('hr_attendance_engine.absence_type_lactation'))
        self.assertAlmostEqual(lactation.duration_hours, 2)  # the rest day takes none
        for day in (MONDAY, MONDAY + 2 * ONE_DAY):
            self.punch(('08:00', '13:00'), ('14:00', '16:00'), day=day)
        monday, rest, wednesday = self.env['hr.workday']._generate(
            self.employee, MONDAY, MONDAY + 2 * ONE_DAY)['workdays'].sorted('date')
        for wd in (monday, wednesday):
            self.assertAlmostEqual(wd.expected_hours, 7)
            self.assertEqual((wd.early_leave_hours, wd.unauthorized_absence_hours), (0, 0))
        self.assertEqual(rest.day_type, 'rest')

    def test_expired_days_are_still_taken_first(self):
        expired = self.period(date(2023, 1, 1), expiry=date(2025, 1, 1))
        current = self.period(date(2025, 1, 1))
        self.assertEqual(expired.state, 'expired')
        vacation = self.absence(
            unit='day', day_to=MONDAY + 4 * ONE_DAY, state='confirmed',
            type_id=self.env.ref('hr_attendance_engine.absence_type_vacation'))
        vacation.action_approve()
        self.assertEqual(vacation.vacation_period_id, expired)
        self.assertEqual((expired.days_remaining, current.days_remaining), (25, 30))

    def test_vacation_split_between_periods(self):
        older = self.period(date(2024, 1, 1), earned=3)
        newer = self.period(date(2025, 1, 1))
        vacation = self.absence(
            unit='day', day_to=MONDAY + 6 * ONE_DAY, state='confirmed',
            type_id=self.env.ref('hr_attendance_engine.absence_type_vacation'))
        vacation.action_approve()
        pieces = self.env['hr.absence'].search(
            [('employee_id', '=', self.employee.id)], order='date_from')
        self.assertEqual([(p.date_from, p.date_to, p.vacation_period_id) for p in pieces], [
            (MONDAY, MONDAY + 2 * ONE_DAY, older),
            (MONDAY + 3 * ONE_DAY, MONDAY + 6 * ONE_DAY, newer),
        ])
        self.assertEqual(set(pieces.mapped('state')), {'approved'})
        self.assertEqual((older.state, newer.days_remaining), ('exhausted', 26))
