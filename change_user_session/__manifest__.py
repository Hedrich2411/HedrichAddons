# -*- coding: utf-8 -*-
{
    'name': 'Change User Session',
    'version': '19.0.1.0.0',
    'summary': 'Switch between users from the systray without logging out',
    'description': """
Change User Session
===================

Lets administrators and support staff switch into another user's session
directly from the Odoo top bar — no logout, no password required.

Use cases
---------
* Reproduce a user's view to debug permission or record-rule issues.
* Validate that workflows behave as expected for a given role.
* Provide support without asking end users for their credentials.

Usage
-----
1. Add the user to the **Change Session Manager** security group.
2. Click the user icon that appears in the top-right systray.
3. Pick the target user and confirm — Odoo reloads with the new session.

Security
--------
This module grants effective impersonation. Restrict the
**Change Session Manager** group to fully trusted administrators.
All actions performed after switching are recorded against the
target user, exactly as if they had logged in themselves.
""",
    'author': 'Hedrich',
    'website': 'https://github.com/Hedrich2411',
    'support': 'hedrich2411@gmail.com',
    'category': 'Tools',
    'depends': ['base', 'web'],
    'data': [
        'security/change_user_session_group.xml',
        'security/ir.model.access.csv',
        'wizard/change_user_session_wizard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'change_user_session/static/src/components/UserSystray/UserSystray.js',
            'change_user_session/static/src/components/UserSystray/UserSystray.xml',
        ],
    },
    'images': ['static/description/banner.png'],
    'application': True,
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
