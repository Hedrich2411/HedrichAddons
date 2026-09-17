# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, _
from odoo.exceptions import UserError
from odoo.http import request

_logger = logging.getLogger(__name__)


class ChangeUserSessionWizard(models.TransientModel):
    _name = 'change.user.session.wizard'
    _description = 'Change User Session Wizard'

    user_id = fields.Many2one("res.users", string="User", required=True)

    def change_session(self):
        self.ensure_one()
        if not request:
            raise UserError(_('The current HTTP application could not be obtained.'))

        target_user = self.user_id
        request.session.logout(keep_db=True)
        request.session.uid = target_user.id
        request.session.login = target_user.login
        request.session.session_token = target_user._compute_session_token(request.session.sid)
        request.update_env(user=target_user.id)

        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }
