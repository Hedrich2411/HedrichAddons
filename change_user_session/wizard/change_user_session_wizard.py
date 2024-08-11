# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.http import request

_logger = logging.getLogger(__name__)

class ChangeUserSessionWizard(models.TransientModel):
    """
    This model represents a wizard for changing the user session in Odoo.
    """

    _name = 'change.user.session.wizard'
    _description = 'Change User Session Wizard'

    user_id = fields.Many2one("res.users", string="User", required=True)

    def change_session(self):
        """
        Change the user session to the selected user.

        This method is called when the user clicks on the "Change Session" button in the wizard.
        It updates the session information to the selected user and reloads the page.

        Returns:
            dict: A dictionary representing the action to be performed after changing the session.
                  In this case, it returns an action to reload the page.
        Raises:
            UserError: If the current HTTP request cannot be obtained.
        """
        self.ensure_one()
        if request:
            request.session.logout()
            request.session.db = request.env.cr.dbname
            request.session.uid = self.user_id.id
            request.session.login = self.user_id.login
            request.session.session_token = self.user_id._compute_session_token(request.session.sid)
            request.env = api.Environment(request.cr, self.user_id.id, {})

            return {
                'type': 'ir.actions.client',
                'tag': 'reload',
            }
        else:
            raise UserError(_('No se pudo obtener la solicitud HTTP actual.'))