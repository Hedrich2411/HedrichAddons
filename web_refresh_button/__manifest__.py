# Part of web_refresh_button. See LICENSE file for full copyright and licensing details.
{
    "name": "Control Panel Refresh Button",
    "version": "19.0.1.0.0",
    "summary": "Add a refresh button to list, kanban and other views to reload "
               "data without reloading the whole page",
    "description": """
Control Panel Refresh Button
============================
Adds a refresh button to the control panel of every backend view (list,
kanban, pivot, graph, calendar, form, ...). Clicking it reloads the current
view's data via Odoo's native ``soft_reload`` client action — no full browser
page reload, no losing your place in the breadcrumb.
""",
    "category": "Tools",
    "author": "Hedrich",
    "website": "https://github.com/Hedrich2411",
    "support": "hedrich2411@gmail.com",
    "license": "LGPL-3",
    "depends": ["web"],
    "images": ["static/description/banner.png"],
    "assets": {
        "web.assets_backend": [
            "web_refresh_button/static/src/refresh_button.xml",
        ],
    },
    "installable": True,
}
