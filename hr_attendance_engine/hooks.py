# -*- coding: utf-8 -*-

def post_init_hook(env):
    """Turn off the calendar-based attendance settings on existing companies.

    Their UI is hidden by this module (they compute against
    resource_calendar_id, which hr.schedule replaces), so a company left
    with them enabled would keep running them with no way to switch them
    off from the interface.
    """
    env['res.company'].search([]).write({
        'auto_check_out': False,
        'absence_management': False,
        'hr_attendance_display_overtime': False,
    })
