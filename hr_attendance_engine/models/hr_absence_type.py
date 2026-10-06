# -*- coding: utf-8 -*-
from odoo import fields, models


CATEGORY_SELECTION = [
    ('vacation', 'Vacation'),
    ('sick', 'Sick'),
    ('permission', 'Permission'),
    ('maternity', 'Maternity'),
    ('paternity', 'Paternity'),
    ('mourning', 'Mourning'),
    ('marriage', 'Marriage'),
    ('unpaid', 'Unpaid leave'),
    ('other', 'Other'),
]

UNIT_SELECTION = [
    ('day', 'Day'),
    ('half_day', 'Half day'),
    ('hour', 'Hour'),
]


class HrAbsenceType(models.Model):
    _name = 'hr.absence.type'
    _description = 'Absence Type'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        required=True,
        help="Short code used by payroll integrations (e.g. VAC, MED, MAT).",
    )
    sequence = fields.Integer(default=10)
    category = fields.Selection(
        CATEGORY_SELECTION, required=True, default='other',
    )
    is_paid = fields.Boolean(
        default=True,
        help="Whether the employee keeps full pay during this absence.",
    )
    consumes_vacation_period = fields.Boolean(
        help="If set, approved absences of this type are deducted from "
             "the employee's open vacation period.",
    )
    consumes_compensatory_balance = fields.Boolean(
        string='Consumes compensatory balance',
        help="If set, approved absences of this type are deducted from "
             "the employee's compensatory hours balance instead of "
             "vacation period or paid leave.",
    )
    default_unit = fields.Selection(
        UNIT_SELECTION, required=True, default='day',
    )
    requires_document = fields.Boolean(
        help="If set, an approved request requires at least one attached "
             "document (CITT, marriage certificate, etc.).",
    )
    color = fields.Integer()
    active = fields.Boolean(default=True)

    _code_unique = models.Constraint(
        'UNIQUE(code)',
        'Absence type code must be unique.',
    )
