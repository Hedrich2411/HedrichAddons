# -*- coding: utf-8 -*-
{
    'name': 'HR Attendance Engine',
    'version': '19.0.1.1.0',
    'summary': 'Per-day schedule assignment and attendance calculation engine',
    'description': """
HR Attendance Engine
====================

Replaces the calendar-based logic of Odoo's attendance app:

* Per-day schedule assignment (planned entry/exit, breaks, tolerances,
  cross-midnight shifts) instead of a fixed weekly recurring calendar.
* A calculation engine that pairs each day's punches against the planned
  shift and produces worked, late, early leave, unauthorized absence and
  holiday hours, frozen in a lockable workday snapshot for payroll.
* Attendance review grid to clean the punches before processing.
* Absences (hourly, half-day, full-day), Peruvian vacation periods,
  public holidays and overtime / compensatory hours. Replaces Time Off
  (hr_holidays), which cannot be installed alongside.
* Attendance, lateness, absence and vacation PDF reports.
""",
    'author': 'Hedrich',
    'website': 'https://github.com/Hedrich2411',
    'support': 'hedrich2411@gmail.com',
    'category': 'Human Resources/Attendances',
    'depends': [
        'hr',
        'hr_attendance',
        'mail',
        'resource',
    ],
    # hr.absence replaces Time Off: two absence systems would disagree.
    'excludes': ['hr_holidays'],
    'data': [
        'security/ir.model.access.csv',
        'security/hr_attendance_engine_security.xml',
        'wizards/hr_schedule_assignment_wizard_views.xml',
        'wizards/hr_workday_generate_wizard_views.xml',
        'wizards/hr_attendance_report_wizard_views.xml',
        'report/hr_attendance_report_actions.xml',
        'report/report_styles.xml',
        'report/hr_attendance_report_templates.xml',
        'report/hr_lateness_report_templates.xml',
        'report/hr_absence_report_templates.xml',
        'report/hr_vacation_report_templates.xml',
        'views/hr_absence_type_views.xml',
        'views/hr_vacation_period_views.xml',
        'views/hr_absence_views.xml',
        'views/hr_workday_overtime_views.xml',
        'views/hr_schedule_views.xml',
        'views/hr_schedule_assignment_views.xml',
        'views/hr_workday_views.xml',
        'views/res_config_settings_views.xml',
        'views/public_holiday_views.xml',
        'views/resource_hide_views.xml',
        'views/hr_attendance_settings_hide.xml',
        'views/hr_workday_analysis_views.xml',
        'views/hr_employee_views.xml',
        'views/hr_attendance_inherit.xml',
        'views/menu.xml',
        'views/attendance_review_views.xml',
        'data/hr_absence_type_data.xml',
        'data/ir_cron_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'hr_attendance_engine/static/src/attendance_review/*.js',
            'hr_attendance_engine/static/src/attendance_review/*.xml',
            'hr_attendance_engine/static/src/attendance_review/*.scss',
        ],
    },
    'images': ['static/description/banner.png'],
    'post_init_hook': 'post_init_hook',
    'application': False,
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
