# -*- coding: utf-8 -*-
"""Datos de prueba realistas para hr_attendance_engine (Perú, America/Lima).

    cmd:  set PYTHONUTF8=1 && .venv\\Scripts\\python.exe odoo-bin shell -c odoo.conf -d demo --no-http < extra-addons\\mock_data\\hr_attendance_engine.py
    bash: PYTHONUTF8=1 .venv/Scripts/python.exe odoo-bin shell -c odoo.conf -d demo --no-http < extra-addons/mock_data/hr_attendance_engine.py

PYTHONUTF8 hace que Python lea el script en UTF-8 (lleva tildes). PowerShell no
admite "<": usa cmd o bash.

Se puede volver a correr: borra los empleados con la etiqueta "Datos de prueba"
junto con todo lo suyo y los crea de nuevo. Compañías, usuarios, plantillas y
feriados se reutilizan si ya existen.

Las fechas son relativas a hoy:
  * M0 (hace dos meses): jornadas procesadas y BLOQUEADAS (planilla cerrada).
  * M1 (mes anterior):   jornadas procesadas; dos volvieron a borrador porque
                         cambió el dato fuente después de procesarlas.
  * mes actual a ayer:   sin procesar, una parte revisada en el tareo.
  * hoy:                 marcación abierta de quien ya entró.

Cada caso del motor tiene una fecha concreta en CASES y en el resumen final.
"""
import base64
import random
from datetime import datetime, timedelta

import pytz
from dateutil.easter import easter
from dateutil.relativedelta import relativedelta

TZ = 'America/Lima'
TAG = 'Datos de prueba'
ONE = timedelta(days=1)
MON, TUE, WED, THU, FRI, SAT, SUN = range(7)
rnd = random.Random(2411)

NOW = datetime.now(pytz.timezone(TZ))
TODAY = NOW.date()
M2 = TODAY.replace(day=1)
M1 = (M2 - ONE).replace(day=1)
M0 = (M1 - ONE).replace(day=1)
END = TODAY - ONE                                  # last day with punches
PLAN_END = M2 + relativedelta(months=1) - ONE      # schedules planned ahead

Workday = env['hr.workday']
Attendance = env['hr.attendance']
Absence = env['hr.absence']
Overtime = env['hr.workday.overtime']


def nth(month, weekday, n):
    """n-th ``weekday`` (1 = first) of the month starting on ``month``."""
    return month + timedelta(days=(weekday - month.weekday()) % 7, weeks=n - 1)


def days(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def get_or_create(model, domain, vals):
    return env[model].search(domain, limit=1) or env[model].create(vals)


# ----------------------------------------------------------------------
# 0. Clean the previous run
# ----------------------------------------------------------------------
tag = get_or_create('hr.employee.category', [('name', '=', TAG)], {'name': TAG, 'color': 3})
old = env['hr.employee'].with_context(active_test=False).search([('category_ids', 'in', tag.ids)])
if old:
    workdays = Workday.search([('employee_id', 'in', old.ids)])
    workdays.filtered(lambda w: w.state == 'locked').write({'state': 'processed'})
    workdays.unlink()
    Overtime.search([('employee_id', 'in', old.ids)]).unlink()
    Absence.search([('employee_id', 'in', old.ids)]).unlink()
    env['hr.vacation.period'].search([('employee_id', 'in', old.ids)]).unlink()
    Attendance.with_context(active_test=False).search([('employee_id', 'in', old.ids)]).unlink()
    env['hr.schedule.assignment'].search([('employee_id', 'in', old.ids)]).unlink()
    old.unlink()
    print(f"Borrados {len(old)} empleados de la corrida anterior")

# ----------------------------------------------------------------------
# 1. Companies, users, departments
# ----------------------------------------------------------------------
peru = env.ref('base.pe')
main = env.ref('base.main_company')
main.write({'workday_tolerance_loss_mode': 'lenient', 'country_id': peru.id})
andinos = get_or_create('res.company', [('name', '=', 'Servicios Andinos de Seguridad S.A.C.')], {
    'name': 'Servicios Andinos de Seguridad S.A.C.',
    'country_id': peru.id, 'city': 'Arequipa', 'vat': '20601234567',
})
andinos.workday_tolerance_loss_mode = 'strict'
companies = main | andinos
env.ref('base.user_admin').write({'company_ids': [(4, andinos.id)], 'tz': TZ})


def user(login, name, groups):
    found = env['res.users'].with_context(active_test=False).search([('login', '=', login)])
    return found or env['res.users'].create({
        'login': login, 'password': login, 'name': name, 'tz': TZ,
        'company_id': main.id, 'company_ids': [(6, 0, companies.ids)],
        'group_ids': [(6, 0, [env.ref(g).id for g in ['base.group_user', *groups]])],
    })


hr_user = user('rrhh', 'Patricia Salinas Ugarte', [
    'hr.group_hr_user', 'hr_attendance.group_hr_attendance_user'])
supervisor = user('supervisor', 'Raúl Ventura Cárdenas', [
    'hr_attendance.group_hr_attendance_officer'])

dept = {}
for key, name, company in [
    ('adm', 'Administración y Finanzas', main), ('ops', 'Operaciones - Planta Ate', main),
    ('ven', 'Ventas - Tienda Miraflores', main), ('ti', 'Sistemas', main),
    ('seg', 'Seguridad y Vigilancia', andinos),
]:
    dept[key] = get_or_create('hr.department', [('name', '=', name), ('company_id', '=', company.id)],
                              {'name': name, 'company_id': company.id})

# ----------------------------------------------------------------------
# 2. Schedule templates
# ----------------------------------------------------------------------
def schedule(name, company, spec, color=0):
    """spec: {weekday: (entry, exit, (break_start, break_end) | None, (tol_in, tol_out))};
    weekdays left out are rest days."""
    found = env['hr.schedule'].search([('name', '=', name), ('company_id', '=', company.id)])
    if found:
        return found
    lines = []
    for d in range(7):
        if d not in spec:
            lines.append((0, 0, {'day_of_week': str(d), 'is_rest_day': True}))
            continue
        entry, exit_, brk, tol = spec[d]
        lines.append((0, 0, {
            'day_of_week': str(d), 'planned_entry': entry, 'planned_exit': exit_,
            'entry_tolerance_minutes': tol[0], 'exit_tolerance_minutes': tol[1],
            'has_break': bool(brk), 'break_start': brk[0] if brk else 0.0,
            'break_end': brk[1] if brk else 0.0,
        }))
    return env['hr.schedule'].create({'name': name, 'company_id': company.id,
                                      'color': color, 'line_ids': lines})


def every(days_, *shift):
    return {d: shift for d in days_}


WEEK, MON_FRI, MON_SAT = range(7), range(5), range(6)
OFI = schedule('Oficina L-V 08:00-17:00', main, every(MON_FRI, 8, 17, (13, 14), (10, 5)), 1)
OFI_SAB = schedule('Oficina L-S (sábado 08:00-13:00)', main, {
    **every(MON_FRI, 8, 17, (13, 14), (10, 5)), SAT: (8, 13, None, (10, 5))}, 2)
TIENDA = schedule('Tienda Ma-Do 10:00-19:00 (descanso lunes)', main,
                  every(range(1, 7), 10, 19, (14, 15), (5, 5)), 3)
MANANA = schedule('Planta turno mañana 06:00-14:00', main, every(MON_SAT, 6, 14, (10, 10.5), (5, 0)), 4)
TARDE = schedule('Planta turno tarde 14:00-22:00', main, every(MON_SAT, 14, 22, (18, 18.5), (5, 0)), 5)
NOCHE = schedule('Planta turno noche 22:00-06:00', main, every(MON_SAT, 22, 6, (2, 2.5), (5, 0)), 6)
CIERRE = schedule('Inventario cierre 16:00-00:00', main, every(WEEK, 16, 0, (20, 20.5), (0, 0)), 7)
PART = schedule('Medio tiempo L-V 09:00-13:00', main, every(MON_FRI, 9, 13, None, (5, 5)), 8)
VIG_DIA = schedule('Vigilancia día 07:00-19:00', andinos, every(WEEK, 7, 19, (13, 14), (5, 5)), 9)
VIG_NOCHE = schedule('Vigilancia noche 19:00-07:00', andinos, every(WEEK, 19, 7, (1, 2), (5, 5)), 10)
DESCANSO = schedule('Descanso', andinos, {}, 11)


def line_of(sched, day):
    return sched.line_ids.filtered(lambda l: l.day_of_week == str(day.weekday()))[:1] \
        if sched else env['hr.schedule.line']


# ----------------------------------------------------------------------
# 3. Public holidays: Peru's national ones, plus one of Servicios Andinos
# ----------------------------------------------------------------------
def peru_holidays(year):
    e = easter(year)
    fixed = [(1, 1, 'Año Nuevo'), (5, 1, 'Día del Trabajo'),
             (6, 7, 'Batalla de Arica y Día de la Bandera'), (6, 29, 'San Pedro y San Pablo'),
             (7, 23, 'Día de la Fuerza Aérea del Perú'), (7, 28, 'Fiestas Patrias'),
             (7, 29, 'Fiestas Patrias'), (8, 6, 'Batalla de Junín'),
             (8, 30, 'Santa Rosa de Lima'), (10, 8, 'Combate de Angamos'),
             (11, 1, 'Día de Todos los Santos'), (12, 8, 'Inmaculada Concepción'),
             (12, 9, 'Batalla de Ayacucho'), (12, 25, 'Navidad')]
    return [(e - 3 * ONE, 'Jueves Santo'), (e - 2 * ONE, 'Viernes Santo')] + [
        (e.replace(month=m, day=d), name) for m, d, name in fixed]


for year in sorted({M0.year, PLAN_END.year}):
    for day, name in peru_holidays(year):
        get_or_create('hr.public.holiday', [('date', '=', day), ('company_id', '=', False)],
                      {'name': name, 'date': day, 'company_id': False})
ANNIVERSARY = M1 + timedelta(days=14)
get_or_create('hr.public.holiday', [('date', '=', ANNIVERSARY), ('company_id', '=', andinos.id)],
              {'name': 'Aniversario de Servicios Andinos', 'date': ANNIVERSARY, 'company_id': andinos.id})
national = {h.date for h in env['hr.public.holiday'].search([('company_id', '=', False)])}
# Who stays home on a holiday: office and plant do, the guards keep the 24/7 rota.
stays_home = {main: national, andinos: set()}
weekday_holiday = next((d for d in sorted(national) if M0 <= d < M1 and d.weekday() < SUN), None)

# ----------------------------------------------------------------------
# 4. Employees and who works when
# ----------------------------------------------------------------------
def rotation_plant(day):
    """Weekly rotation mañana → tarde → noche, Sunday off (template rest line)."""
    return (MANANA, TARDE, NOCHE)[day.isocalendar()[1] % 3]


def guard_cycle(offset):
    """4x2: two day shifts, two night shifts, two days off."""
    pattern = (VIG_DIA, VIG_DIA, VIG_NOCHE, VIG_NOCHE, DESCANSO, DESCANSO)
    return lambda day: pattern[((day - M0).days + offset) % 6]


SOFIA_START = nth(M1, MON, 3)
INDUCTION = SOFIA_START - 3 * ONE
CIERRE_DAY = nth(M1, FRI, 2)
victor_offset = -(ANNIVERSARY - M0).days % 6   # on a day shift the anniversary

PEOPLE = [
    # key, name, sex, company, dept, job, hired, schedule(day), manager
    ('ana', 'Ana Lucía Torres Vega', 'female', main, 'adm', 'Contadora general',
     '2019-03-15', lambda d: OFI, hr_user),
    ('carlos', 'Carlos Alberto Mendoza Ríos', 'male', main, 'adm', 'Asistente contable',
     '2021-07-01', lambda d: OFI, hr_user),
    ('maria', 'María Fernanda Quispe Huamán', 'female', main, 'ven', 'Asesora de ventas',
     '2022-02-10', lambda d: TIENDA, hr_user),
    ('jose', 'José Luis Huamán Flores', 'male', main, 'ops', 'Operario de producción',
     '2018-11-05', rotation_plant, supervisor),
    ('rosa', 'Rosa Elena Paredes Castillo', 'female', main, 'adm', 'Asistente de RR.HH.',
     '2020-05-18', lambda d: OFI_SAB, hr_user),
    ('jorge', 'Jorge Enrique Chávez Salazar', 'male', main, 'ops', 'Operador de calderos',
     '2017-08-21', lambda d: NOCHE, supervisor),
    ('lucia', 'Lucía Ramírez Gutiérrez', 'female', main, 'ven', 'Practicante de marketing',
     '2025-04-01', lambda d: PART, hr_user),
    ('pedro', 'Pedro Pablo Sánchez Vargas', 'male', main, 'adm', 'Analista de compras',
     '2021-01-11', lambda d: OFI, hr_user),
    ('carmen', 'Carmen Rosa Delgado Ponce', 'female', main, 'adm', 'Tesorera',
     '2016-09-01', lambda d: OFI, hr_user),
    ('miguel', 'Miguel Ángel Rojas León', 'male', main, 'ven', 'Jefe de tienda',
     '2019-12-02', lambda d: TIENDA, hr_user),
    ('daniela', 'Daniela Vásquez Cruz', 'female', main, 'ti', 'Analista de sistemas',
     '2023-06-12', lambda d: OFI, hr_user),
    ('ricardo', 'Ricardo Flores Medina', 'male', main, 'ops', 'Almacenero',
     '2020-10-19', lambda d: CIERRE if d == CIERRE_DAY else MANANA, supervisor),
    ('sofia', 'Sofía Castro Núñez', 'female', main, 'adm', 'Asistente administrativa',
     str(SOFIA_START), lambda d: OFI if d >= SOFIA_START else None, hr_user),
    ('andres', 'Andrés Morales Paz', 'male', main, 'adm', 'Analista de créditos',
     '2022-08-08', lambda d: OFI, hr_user),
    ('victor', 'Víctor Hugo Ccori Mamani', 'male', andinos, 'seg', 'Agente de seguridad',
     '2021-03-01', guard_cycle(victor_offset), supervisor),
    ('wilber', 'Wilber Condori Apaza', 'male', andinos, 'seg', 'Agente de seguridad',
     '2022-09-15', guard_cycle(victor_offset + 2), supervisor),
    ('elmer', 'Elmer Choque Ticona', 'male', andinos, 'seg', 'Supervisor de turno',
     '2019-05-20', guard_cycle(victor_offset + 4), supervisor),
]

E, PLAN, COMPANY = {}, {}, {}
for key, name, sex, company, dkey, job, hired, plan, manager in PEOPLE:
    first = name.split()[0].lower()
    last = name.split()[-2].lower() if len(name.split()) > 2 else name.split()[-1].lower()
    E[key] = env['hr.employee'].create({
        'name': name, 'company_id': company.id, 'department_id': dept[dkey].id,
        'job_title': job, 'tz': TZ, 'sex': sex, 'category_ids': [(6, 0, tag.ids)],
        'identification_id': str(rnd.randint(10_000_000, 79_999_999)),
        'birthday': f"{rnd.randint(1972, 2002)}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}",
        'work_email': f"{first}.{last}@example.pe".translate(str.maketrans('áéíóúñ', 'aeioun')),
        'mobile_phone': f"+51 9{rnd.randint(10, 99)} {rnd.randint(100, 999)} {rnd.randint(100, 999)}",
        'contract_date_start': hired,
        'attendance_manager_id': manager.id,
    })
    PLAN[key], COMPANY[key] = plan, company

env['hr.schedule.assignment'].create([
    {'employee_id': E[key].id, 'date': day, 'schedule_id': sched.id}
    for key in E for day in days(M0, PLAN_END)
    if (sched := PLAN[key](day))
])

# ----------------------------------------------------------------------
# 5. Vacation periods: 30 days per year of service, taken the year after
# ----------------------------------------------------------------------
def periods(key, sold=0.0, closed_oldest=False):
    """Oldest one expired, the previous cycle usable now, the current cycle
    still being earned (advance leave)."""
    hired = E[key].contract_date_start
    anniversary = hired.replace(year=TODAY.year)
    if anniversary > TODAY:
        anniversary = anniversary.replace(year=TODAY.year - 1)
    out = {}
    for back in (2, 1, 0):
        start = anniversary.replace(year=anniversary.year - back)
        if start < hired:
            continue
        cycle_end = start + relativedelta(years=1) - ONE
        out[back] = env['hr.vacation.period'].create({
            'employee_id': E[key].id, 'cycle_start': start, 'cycle_end': cycle_end,
            'expiry_date': cycle_end + relativedelta(years=1), 'days_earned': 30,
            'days_paid_cash': sold if back == 1 else 0.0,
            'is_closed': closed_oldest and back == 2,
        })
    return out


PERIODS = {key: periods(key, sold=15.0 if key == 'andres' else 0.0, closed_oldest=key == 'carmen')
           for key in E}

# ----------------------------------------------------------------------
# 6. Absences
# ----------------------------------------------------------------------
PDF = base64.b64encode(
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>"
    b"endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n")
TYPE = {code: env.ref(f'hr_attendance_engine.absence_type_{x}') for code, x in [
    ('VAC', 'vacation'), ('MED', 'sick'), ('MAT', 'maternity'), ('PAT', 'paternity'),
    ('LUT', 'mourning'), ('MAR', 'marriage'), ('PCG', 'permission_paid'),
    ('PSG', 'permission_unpaid'), ('PCM', 'permission_compensatory'), ('LAC', 'lactation')]}


def absence(key, code, date_from, date_to=None, state='approved', reason='', document=None, **vals):
    unit = vals.pop('unit', TYPE[code].default_unit)
    rec = Absence.create({
        'employee_id': E[key].id, 'type_id': TYPE[code].id, 'unit': unit,
        'date_from': date_from, 'date_to': date_to or date_from, 'reason': reason, **vals,
    })
    if document:
        rec.attachment_ids = [(0, 0, {'name': document, 'datas': PDF, 'mimetype': 'application/pdf',
                                       'res_model': 'hr.absence', 'res_id': rec.id})]
    if state != 'draft':
        rec.action_confirm()
    if state == 'approved':
        rec.action_approve()
    if state == 'rejected':
        rec.action_reject()
    if state == 'cancelled':
        rec.action_cancel()
    return rec


def hours(key, code, day, h_from, h_to, **kw):
    return absence(key, code, day, unit='hour', hour_from=h_from, hour_to=h_to, **kw)


# Vacation history, before the window: the oldest cycle was enjoyed in full,
# except Jorge, who still has 15 expired days owed; Miguel also took 20 days
# of the next one, so his October trip has to borrow from the current cycle.
for key, by_back in PERIODS.items():
    oldest = by_back.get(2)
    if oldest and not oldest.is_closed:
        taken = 15 if key == 'jorge' else 30
        start = oldest.cycle_end + 15 * ONE
        absence(key, 'VAC', start, start + (taken - 1) * ONE, reason=f"Vacaciones {oldest.name}")
miguel_prev = PERIODS['miguel'][1]
absence('miguel', 'VAC', miguel_prev.cycle_end + 20 * ONE, miguel_prev.cycle_end + 39 * ONE,
        reason=f"Vacaciones {miguel_prev.name} (primera parte)")
MIGUEL_TRIP = nth(M2, MON, 4)
absence('miguel', 'VAC', MIGUEL_TRIP, MIGUEL_TRIP + 14 * ONE, reason='Viaje a Chachapoyas')
LACTATION = (nth(M1, MON, 4), PLAN_END)
absence('maria', 'LAC', *LACTATION, unit='hour', hour_from=18, hour_to=19,
        reason='Hora de lactancia hasta que su hija cumpla un año (Ley 27240)')

absence('ana', 'VAC', nth(M1, MON, 2), nth(M1, MON, 2) + 6 * ONE, reason='Vacaciones familiares a Cusco')
absence('maria', 'MED', nth(M1, TUE, 2), nth(M1, TUE, 2) + 2 * ONE,
        reason='Faringitis aguda, descanso médico EsSalud', document='CITT-2026-0048213.pdf')
absence('maria', 'PCG', nth(M1, SAT, 3), unit='half_day', half_day_period='am',
        reason='Control prenatal de su hermana (acompañante)')
hours('rosa', 'PCG', nth(M1, MON, 1), 8, 10, reason='Cita médica en EsSalud')
hours('rosa', 'PCG', nth(M1, WED, 1), 15, 17, reason='Trámite notarial')
hours('rosa', 'PCG', nth(M1, TUE, 2), 10, 11.5, reason='Renovación de DNI en RENIEC')
hours('rosa', 'PCG', nth(M1, TUE, 3), 10, 11, reason='Reunión en el colegio de su hijo')
hours('pedro', 'PSG', nth(M0, THU, 2), 15, 17, reason='Asunto personal')
absence('pedro', 'PAT', nth(M1, WED, 1), nth(M1, WED, 1) + 9 * ONE,
        reason='Nacimiento de su hija (Ley 29409)')
absence('carmen', 'MAT', M1 + 13 * ONE, M1 + 110 * ONE, reason='Descanso pre y post natal',
        document='CITT-maternidad-2026-0031177.pdf')
absence('miguel', 'LUT', nth(M0, TUE, 3), nth(M0, TUE, 3) + 4 * ONE, reason='Fallecimiento de su padre')
hours('miguel', 'PSG', nth(M1, SAT, 1), 10, 12, reason='Mudanza')
absence('daniela', 'MAR', nth(M0, MON, 4), nth(M0, MON, 4) + 4 * ONE, reason='Matrimonio civil (beneficio de la empresa)')
absence('andres', 'VAC', nth(M0, MON, 1), nth(M0, MON, 1) + 14 * ONE, reason='Vacaciones (15 días vendidos)')
hours('andres', 'PCG', nth(M1, FRI, 4), 15, 17, state='rejected', reason='Partido de fulbito')
absence('andres', 'VAC', nth(M2, MON, 3), nth(M2, MON, 3) + 4 * ONE, state='cancelled',
        reason='Viaje postergado')
hours('andres', 'PCG', nth(M2, WED, 3), 9, 11, state='draft', reason='Cita en SUNAT')
hours('lucia', 'PCG', nth(M2, FRI, 3), 9, 13, state='confirmed', reason='Examen parcial en la universidad')

# ----------------------------------------------------------------------
# 7. Overtime / compensatory, registered before the workdays exist
# ----------------------------------------------------------------------
def overtime(key, day, h_from, h_to, type_='paid', state='approved', reason=''):
    rec = Overtime.create({'employee_id': E[key].id, 'date': day, 'type': type_,
                           'hour_from': h_from, 'hour_to': h_to, 'reason': reason})
    if state == 'approved':
        rec.action_approve()
    elif state == 'rejected':
        rec.action_reject()
    return rec


def after_shift(key, day, length):
    """(from, to) of ``length`` hours right after the shift, capped at 24:00."""
    exit_ = line_of(PLAN[key](day), day).planned_exit
    return exit_, min(exit_ + length, 24.0)


overtime('rosa', nth(M0, SAT, 1), 13, 17, 'compensatory', reason='Cierre de planilla')
overtime('rosa', nth(M0, SAT, 2), 13, 17, 'compensatory', reason='Auditoría SUNAFIL')
JOSE_OT = nth(M1, FRI, 2)
overtime('jose', JOSE_OT, *after_shift('jose', JOSE_OT, 2), reason='Pedido urgente de cliente')
overtime('jose', nth(M1, FRI, 3), *after_shift('jose', nth(M1, FRI, 3), 1.5), state='draft',
         reason='Mantenimiento de línea')
overtime('jose', nth(M0, SAT, 2), *after_shift('jose', nth(M0, SAT, 2), 3), state='rejected',
         reason='Sin orden del jefe')
overtime('ricardo', nth(M0, WED, 2), 14, 16, reason='Recepción de mercadería')
overtime('sofia', SOFIA_START + ONE, 17, 18.5, state='draft', reason='Cierre de mes')

# The compensatory permission needs the 8h balance approved above.
hours('rosa', 'PCM', nth(M1, THU, 3), 8, 12, reason='Compensa horas de agosto')

# ----------------------------------------------------------------------
# 8. Punches
# ----------------------------------------------------------------------
# (start, end) local hours. An hour earlier than the shift entry falls the
# next morning on a night shift; None leaves the punch open.
CASES = {
    # Tardanzas y salidas (tolerancia de entrada 10 min, de salida 5 min)
    ('carlos', nth(M1, TUE, 1)): [(8 + 7 / 60, 13), (14, 17.05)],        # 8 min: dentro
    ('carlos', nth(M1, THU, 1)): [(8 + 25 / 60, 13), (14, 17.1)],        # 25 min: 15 tarde
    ('carlos', nth(M1, WED, 2)): [(7.95, 13), (14 + 40 / 60, 17.05)],    # refrigerio de 1h40
    ('carlos', nth(M1, FRI, 2)): [(7.9, 13), (14, 17 - 3 / 60)],          # sale 3 min antes
    ('carlos', nth(M1, FRI, 3)): [(7.9, 13), (14, 16 + 20 / 60)],         # sale 40 min antes
    ('carlos', nth(M0, MON, 3)): [(9.5, 13), (14, 17)],                   # 1h30 tarde (agosto)
    ('carlos', nth(M1, TUE, 3)): [(7.95, 13), (14, 16)],                  # corregida luego
    ('andres', nth(M1, MON, 1)): [(7.95, 13), (14, 17 - 2 / 60)],
    ('andres', nth(M1, THU, 2)): [(7.95, 13), (14, 15.5)],
    ('andres', nth(M1, WED, 3)): [],                                       # falta
    ('andres', nth(M1, THU, 3)): [],                                       # falta
    # Permisos por horas: al inicio, al final, a media jornada, regreso tarde
    ('rosa', nth(M1, MON, 1)): [(10, 13), (14, 17.05)],
    ('rosa', nth(M1, WED, 1)): [(7.95, 13), (14, 15)],
    ('rosa', nth(M1, TUE, 2)): [(7.92, 10), (11.5, 13), (14, 17.02)],
    ('rosa', nth(M1, TUE, 3)): [(7.95, 10), (11 + 40 / 60, 13), (14, 17)],
    ('rosa', nth(M1, THU, 3)): [(12, 13), (14, 17.05)],                    # compensatorio 08-12
    ('rosa', nth(M1, MON, 4)): [(7.95, 13), (14, 16)],                     # permiso aprobado tarde
    ('rosa', nth(M0, SAT, 1)): [(7.95, 17.05)],                            # sábado + 4h comp.
    ('rosa', nth(M0, SAT, 2)): [(7.9, 17.1)],
    ('pedro', nth(M0, THU, 2)): [(7.95, 13), (14, 15)],
    ('miguel', nth(M1, SAT, 1)): [(12, 14), (15, 19.05)],
    ('maria', nth(M1, SAT, 3)): [(14.95, 19.1)],                           # medio día mañana
    # Marcaciones incompletas o raras
    ('maria', nth(M1, SUN, 1)): [(9.9, 19.05)],                            # no marcó refrigerio
    ('daniela', nth(M1, THU, 2)): [(7.95, 17.1)],                          # no marcó refrigerio
    ('daniela', nth(M1, WED, 4)): [(7.9, 13), (13 + 1 / 60, 13 + 2 / 60), (14, 17.05)],  # doble marcación
    ('daniela', nth(M1, MON, 3)): [(7.9, 7.9 + 1 / 60), (7.9 + 2 / 60, 13), (14, 17.02)],   # se archiva
    ('jorge', nth(M1, WED, 2)): [(21.9, 2), (2.5, None)],                  # olvidó marcar salida
    ('jorge', nth(M1, MON, 3)): [(22.5, 2), (2.5, 6.05)],                  # 30 min tarde
    ('lucia', nth(M1, MON, 2)): [],                                        # falta injustificada
    ('lucia', nth(M0, THU, 3)): [],
    # Trabajo en descanso, feriado y día sin horario
    ('lucia', nth(M1, SAT, 2)): [(9, 13)],                                 # sábado de descanso
    ('miguel', nth(M1, MON, 2)): [(9.95, 14), (15, 19.1)],                 # lunes de descanso
    ('sofia', INDUCTION): [(9, 13)],                                       # inducción sin horario
    ('sofia', SOFIA_START + ONE): [(7.5, 13), (14, 18.5)],                 # llega y se va fuera de hora
    # Turnos cercanos: cierre 16-00 y al día siguiente mañana 06-14
    ('ricardo', CIERRE_DAY): [(15.9, 20), (20.5, 0.1)],
    ('ricardo', CIERRE_DAY + ONE): [(5.85, 10), (10.5, 14.05)],
    ('ricardo', nth(M0, WED, 2)): [(5.9, 10), (10.5, 16.05)],               # + 2h extra
}
if M2 <= END:
    CASES[('carlos', M2)] = [(8, 13), (14, None)]                          # pendiente en el tareo
    if M2.weekday() < SAT:
        CASES[('lucia', M2)] = []
if weekday_holiday:
    CASES[('jose', weekday_holiday)] = None                                # trabaja el feriado

# José: stays 2h after his shift on the overtime day.
jose_line = line_of(PLAN['jose'](JOSE_OT), JOSE_OT)
CASES[('jose', JOSE_OT)] = [
    (jose_line.planned_entry - 0.1, jose_line.break_start),
    (jose_line.break_end, jose_line.planned_exit + 2.05)]

# Guards: Wilber late under strict tolerance, Elmer misses a night and doubles a shift.
SHIFTS = {(key, pick): [d for d in days(M1, M2 - ONE) if PLAN[key](d) == pick]
          for key, pick in [('wilber', VIG_DIA), ('elmer', VIG_NOCHE), ('elmer', VIG_DIA)]}
CASES[('wilber', SHIFTS['wilber', VIG_DIA][0])] = [(7 + 8 / 60, 13), (14, 19.05)]   # 8 min: todo cuenta
CASES[('wilber', SHIFTS['wilber', VIG_DIA][1])] = [(7 + 4 / 60, 13), (14, 19.05)]   # 4 min: perdonado
CASES[('elmer', SHIFTS['elmer', VIG_NOCHE][0])] = []
ELMER_DOUBLE = SHIFTS['elmer', VIG_DIA][1]
CASES[('elmer', ELMER_DOUBLE)] = [(6.9, 13), (14, 23.05)]
overtime('elmer', ELMER_DOUBLE, 19, 23, reason='Cubre ausencia de compañero')


def normal(line):
    """A plausible day: a few minutes early (sometimes within tolerance),
    break on time, out a few minutes after the exit."""
    jitter = lambda a, b: rnd.uniform(a, b) / 60
    if rnd.random() < 0.15 and line.entry_tolerance_minutes > 1:
        arrive = line.planned_entry + jitter(1, line.entry_tolerance_minutes - 1)
    else:
        arrive = line.planned_entry - jitter(1, 14)
    leave = line.planned_exit + jitter(0, 12)
    if line.has_break:
        return [(arrive, line.break_start + jitter(0, 3)),
                (line.break_end - jitter(0, 4), leave)]
    return [(arrive, leave)]


def to_utc(day, line, hour, first):
    if first or not line:
        return Workday._hour_to_utc(day, hour, TZ)
    return Workday._engine_at(day, line, hour, TZ)


leave_days = {(a.employee_id.id, d) for a in Absence.search([
    ('employee_id', 'in', [e.id for e in E.values()]), ('state', '=', 'approved'), ('unit', '=', 'day')])
    for d in days(a.date_from, a.date_to)}
punch_count = 0
for key, employee in E.items():
    vals_list = []
    for day in days(M0, END):
        line = line_of(PLAN[key](day), day)
        spans = CASES.get((key, day), 'auto')
        if spans is None or (spans == 'auto' and line and not line.is_rest_day
                             and (employee.id, day) not in leave_days
                             and day not in stays_home[COMPANY[key]]):
            spans = normal(line)
            if key == 'maria' and LACTATION[0] <= day <= LACTATION[1]:
                spans[-1] = (spans[-1][0], 18 + rnd.uniform(0, 6) / 60)
        if spans == 'auto':
            continue
        for i, (start, end) in enumerate(spans):
            check_in = to_utc(day, line, start, first=i == 0)
            vals = {'employee_id': employee.id, 'check_in': check_in,
                    'in_mode': 'kiosk', 'out_mode': 'kiosk'}
            if end is not None:
                check_out = to_utc(day, line, end, first=False)
                vals['check_out'] = check_out if check_out > check_in else check_out + ONE
            vals_list.append(vals)
    records = Attendance.create(sorted(vals_list, key=lambda v: v['check_in']))
    punch_count += len(records)
    if key == 'daniela':
        # Tareo: the double tap is archived, not deleted.
        records.filtered(lambda a: a.check_out and a.check_out - a.check_in < timedelta(minutes=5)
                         and a.check_in < Workday._hour_to_utc(nth(M1, MON, 3), 8, TZ)
                         and a.check_in > Workday._hour_to_utc(nth(M1, MON, 3), 7, TZ)
                         ).write({'active': False})

# ----------------------------------------------------------------------
# 9. Tareo, generation, payroll close
# ----------------------------------------------------------------------
everyone = env['hr.employee'].browse([e.id for e in E.values()])
sofia = E['sofia']
reviewer = Workday.with_user(hr_user).with_context(allowed_company_ids=companies.ids)
reviewer.review_mark((everyone - sofia).ids, [str(d) for d in days(M0, M2 - ONE)])
reviewer.review_mark(sofia.ids, [str(d) for d in days(INDUCTION, M2 - ONE)])
Workday._generate(everyone - sofia, M0, M2 - ONE)
Workday._generate(sofia, INDUCTION, M2 - ONE)
Workday.search([('employee_id', 'in', everyone.ids), ('date', '<', M1)]).action_lock()

# After processing September, the source data still moves:
carlos_fix = Attendance.search([('employee_id', '=', E['carlos'].id),
                                ('check_in', '>=', Workday._hour_to_utc(nth(M1, TUE, 3), 13.9, TZ)),
                                ('check_in', '<', Workday._hour_to_utc(nth(M1, TUE, 3), 14.1, TZ))])
carlos_fix.write({'check_out': Workday._hour_to_utc(nth(M1, TUE, 3), 17 + 3 / 60, TZ),
                  'out_mode': 'manual'})                     # supervisor corrects the exit
hours('rosa', 'PCG', nth(M1, MON, 4), 16, 17, reason='Permiso verbal regularizado')
overtime('jose', nth(M1, SAT, 4), *after_shift('jose', nth(M1, SAT, 4), 1), 'compensatory',
         reason='Limpieza de planta')

# Current month: only part of the team reviewed so far.
if M2 <= END:
    reviewer.review_mark([E[k].id for k in ('ana', 'rosa', 'jose', 'victor')],
                        [str(d) for d in days(M2, END)])

# Today: those already at the office are checked in.
for key in ('ana', 'rosa'):
    line = line_of(PLAN[key](TODAY), TODAY)
    if line and not line.is_rest_day and NOW.hour + NOW.minute / 60 > line.planned_entry:
        Attendance.create({'employee_id': E[key].id, 'in_mode': 'kiosk',
                           'check_in': to_utc(TODAY, line, line.planned_entry - 0.1, True)})

env.cr.commit()

# ----------------------------------------------------------------------
# 10. Summary
# ----------------------------------------------------------------------
def count(model, field, domain):
    return ', '.join(f"{value}: {n}" for value, n in env[model]._read_group(domain, [field], ['__count']))


emp_domain = [('employee_id', 'in', everyone.ids)]
wds = Workday.search(emp_domain)
print(f"""
Datos de prueba listos ({M0} -> {END}, hoy {TODAY})
  Empleados:    {len(everyone)} en {len(companies)} compañías ({main.name}: lenient, {andinos.name}: strict)
  Marcaciones:  {punch_count} (+ las abiertas de hoy)
  Jornadas:     {count('hr.workday', 'state', emp_domain)}
  Tipo de día:  {count('hr.workday', 'day_type', emp_domain)}
  Incongruentes: {len(wds.filtered('incongruency_message'))}   Faltas: {len(wds.filtered('is_absent'))}
  Ausencias:    {count('hr.absence', 'state', emp_domain)}
  Horas extra:  {count('hr.workday.overtime', 'state', emp_domain)}
  Vacaciones:   {count('hr.vacation.period', 'state', emp_domain)}
Usuarios (contraseña = login): rrhh (todas las asistencias), supervisor (officer: planta y vigilancia)

Casos para revisar:
  Tardanza dentro / fuera de tolerancia ....... Carlos {nth(M1, TUE, 1)} / {nth(M1, THU, 1)}
  Tardanza en modo strict ..................... Wilber {SHIFTS['wilber', VIG_DIA][0]} (8 min, todo cuenta)
  Refrigerio excedido ......................... Carlos {nth(M1, WED, 2)}
  Salida anticipada dentro / fuera ............ Carlos {nth(M1, FRI, 2)} / {nth(M1, FRI, 3)}
  Permiso al inicio / al final ................ Rosa {nth(M1, MON, 1)} / {nth(M1, WED, 1)}
  Permiso a media jornada con marcación ....... Rosa {nth(M1, TUE, 2)}
  Regreso tarde de un permiso ................. Rosa {nth(M1, TUE, 3)}
  Permiso compensatorio (consume saldo) ....... Rosa {nth(M1, THU, 3)}
  Medio día (mañana) .......................... María {nth(M1, SAT, 3)}
  Descanso médico con CITT .................... María {nth(M1, TUE, 2)}
  Vacaciones ................................. Ana {nth(M1, MON, 2)}
  Días vencidos que se siguen debiendo ........ Jorge (15 días del periodo {PERIODS['jorge'][2].name})
  Vacación partida entre dos periodos ......... Miguel {MIGUEL_TRIP} (10 + 5 días)
  Lactancia: 1 h diaria en un solo registro ... María desde {LACTATION[0]}
  Vacaciones con días vendidos (agotado) ...... Andrés {nth(M0, MON, 1)}
  Maternidad / paternidad / duelo ............. Carmen {M1 + 13 * ONE} / Pedro {nth(M1, WED, 1)} / Miguel {nth(M0, TUE, 3)}
  Falta injustificada ......................... Lucía {nth(M1, MON, 2)}, Andrés {nth(M1, WED, 3)}
  Marcación abierta ........................... Jorge {nth(M1, WED, 2)}
  No marcó refrigerio ......................... Daniela {nth(M1, THU, 2)}, María {nth(M1, SUN, 1)}
  Doble marcación archivada / sin limpiar ..... Daniela {nth(M1, MON, 3)} / {nth(M1, WED, 4)}
  Trabajo en feriado / feriado de la empresa .. José {weekday_holiday} / Víctor {ANNIVERSARY}
  Trabajo en día de descanso .................. Miguel {nth(M1, MON, 2)}, Lucía {nth(M1, SAT, 2)}
  Turno noche cruzando medianoche ............. Jorge (todo el periodo)
  Turnos cercanos (cierre 16-00 y 06-14) ...... Ricardo {CIERRE_DAY} / {CIERRE_DAY + ONE}
  Día sin horario con marcación ............... Sofía {INDUCTION}
  Horas extra pagadas / borrador / rechazada .. José {JOSE_OT} / {nth(M1, FRI, 3)} / {nth(M0, SAT, 2)}
  Jornada vuelta a borrador ................... Carlos {nth(M1, TUE, 3)}, Rosa {nth(M1, MON, 4)}
  Agosto bloqueado (planilla cerrada) ......... {M0} -> {M1 - ONE}
""")
