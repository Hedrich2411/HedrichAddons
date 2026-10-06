# -*- coding: utf-8 -*-
from odoo import api, fields, models


# Fields the engine reads from a punch.
ENGINE_FIELDS = {'check_in', 'check_out', 'employee_id', 'active'}


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    active = fields.Boolean(
        default=True,
        help="Unset to take this punch out of the calculation while keeping "
             "it on record. Used by the attendance review to discard bad "
             "punches without destroying the evidence.",
    )

    def _update_overtime(self, attendance_domain=None):
        """Odoo's overtime rules measure punches against resource.calendar
        and auto-approve the result. Additional hours are hr.workday.overtime
        here, so every native path (punches, rulesets, company settings)
        stops at this one entry point and no overtime line is created.
        """
        return

    # ------------------------------------------------------------------
    # Locked-workday guard
    # ------------------------------------------------------------------
    # A locked hr.workday is a frozen snapshot payroll already consumed.
    # Editing the punches behind it would leave the snapshot stating
    # something the source data no longer says, with nothing flagging the
    # divergence. Unlock the workday to correct the punches, then
    # reprocess. Punches behind a processed workday send it back to draft.

    def _engine_workdays(self):
        """Non-draft workdays these punches feed: the ones that consumed
        them and the ones whose window they now sit in."""
        Workday = self.env['hr.workday']
        consumed = Workday.search([
            ('attendance_ids', 'in', self.ids),
            ('state', '!=', 'draft'),
        ]) if self.ids else Workday
        return consumed | Workday._engine_touched_moments(
            [(a.employee_id, a.check_in) for a in self]
        )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._engine_workdays()._engine_sources_changed()
        return records

    def write(self, vals):
        engine = bool(ENGINE_FIELDS & set(vals))
        if engine:
            self._engine_workdays()._engine_sources_changed()
        res = super().write(vals)
        if engine:
            self._engine_workdays()._engine_sources_changed()
        return res

    def unlink(self):
        self._engine_workdays()._engine_sources_changed()
        return super().unlink()
