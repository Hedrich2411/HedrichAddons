# -*- coding: utf-8 -*-
{
    'name': 'Change User Session',
    'version': '17.0.1.0.0',
    'summary': 'Allows you to change user session',
    'author': 'Hedrich',
    'category': '',
    'depends': ['base', 'web'],
    "data": [
        "security/change_user_session_group.xml",
        "security/ir.model.access.csv",
        "wizard/change_user_session_wizard_views.xml",
    ],
    'assets':{
        'web.assets_backend': [
            'change_user_session/static/src/components/UserSystray/UserSystray.js',
            'change_user_session/static/src/components/UserSystray/UserSystray.xml'
        ],
    },
    'images': ['static/description/banner.png'],
    'application': True,
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
