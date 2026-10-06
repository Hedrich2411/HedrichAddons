/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Layout } from "@web/search/layout";
import { SearchBar } from "@web/search/search_bar/search_bar";
import { useSearchBarToggler } from "@web/search/search_bar/search_bar_toggler";
import { CogMenu } from "@web/search/cog_menu/cog_menu";
import { ViewScaleSelector } from "@web/views/view_components/view_scale_selector";
import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";
import { standardViewProps } from "@web/views/standard_view_props";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { session } from "@web/session";
import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";

const { DateTime } = luxon; // luxon is a global in Odoo, not an importable module

const SCALES = {
    day: { description: _t("Day") },
    week: { description: _t("Week") },
    month: { description: _t("Month") },
};

// Rows are hr.employee; the cells come from hr.workday.review_grid, which
// runs the engine without persisting anything.
export class AttendanceReviewController extends Component {
    static template = "hr_attendance_engine.AttendanceReview";
    static components = { Layout, SearchBar, CogMenu, ViewScaleSelector };
    static props = { ...standardViewProps };

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.searchBarToggler = useSearchBarToggler();

        this.state = useState({
            scale: "week",
            anchor: DateTime.local().startOf("day"),
            dates: [],
            rows: [],
            selected: null, // {employeeId, employeeName, cell}
            loading: true,
        });

        onWillStart(() => this.load(this.props));
        onWillUpdateProps((nextProps) => {
            // The search bar filters the employees, i.e. the grid rows.
            if (JSON.stringify(nextProps.domain) !== JSON.stringify(this.props.domain)) {
                this.load(nextProps);
            }
        });
    }

    get scales() {
        return SCALES;
    }

    // --- range -------------------------------------------------------
    get range() {
        const { scale, anchor } = this.state;
        const unit = scale === "day" ? "day" : scale;
        return {
            start: anchor.startOf(unit),
            end: anchor.endOf(unit),
        };
    }

    get rangeLabel() {
        const { start, end } = this.range;
        if (this.state.scale === "day") {
            return start.toLocaleString(DateTime.DATE_FULL);
        }
        return `${start.toLocaleString(DateTime.DATE_MED)} - ${end.toLocaleString(
            DateTime.DATE_MED
        )}`;
    }

    get reviewedCount() {
        let reviewed = 0;
        let total = 0;
        for (const row of this.state.rows) {
            for (const cell of row.cells) {
                total++;
                if (cell.is_reviewed) {
                    reviewed++;
                }
            }
        }
        return { reviewed, total };
    }

    // --- data --------------------------------------------------------
    async load(props = this.props) {
        this.state.loading = true;
        const employeeIds = await this.orm.search("hr.employee", props.domain || []);
        const { start, end } = this.range;
        const grid = await this.orm.call("hr.workday", "review_grid", [
            employeeIds,
            start.toISODate(),
            end.toISODate(),
        ]);
        this.state.dates = grid.dates;
        this.state.rows = grid.rows;
        this.state.loading = false;
        this.syncSelection();
    }

    /** Keep the side panel pointing at the refreshed cell. */
    syncSelection() {
        if (!this.state.selected) {
            return;
        }
        const { employeeId, cell } = this.state.selected;
        const row = this.state.rows.find((r) => r.id === employeeId);
        const fresh = row && row.cells.find((c) => c.date === cell.date);
        this.state.selected = fresh
            ? { employeeId, employeeName: row.name, cell: fresh }
            : null;
    }

    setScale(scale) {
        this.state.scale = scale;
        this.load();
    }

    shiftRange(direction) {
        const unit = this.state.scale === "day" ? "days" : `${this.state.scale}s`;
        this.state.anchor = this.state.anchor.plus({ [unit]: direction });
        this.load();
    }

    today() {
        this.state.anchor = DateTime.local().startOf("day");
        this.load();
    }

    // --- cells -------------------------------------------------------
    selectCell(row, cell) {
        this.state.selected = { employeeId: row.id, employeeName: row.name, cell };
    }

    isSelected(row, cell) {
        const sel = this.state.selected;
        return sel && sel.employeeId === row.id && sel.cell.date === cell.date;
    }

    /** Bootstrap contextual colour per cell status. */
    cellClass(cell) {
        const map = {
            incongruent: "table-warning",
            absent: "table-danger",
            leave: "table-info",
            holiday: "table-info",
            rest: "text-muted",
            no_schedule: "text-muted",
            ok: "",
        };
        return map[cell.status] || "";
    }

    cellLabel(cell) {
        switch (cell.status) {
            case "incongruent":
                return "⚠";
            case "absent":
                return _t("A");
            case "rest":
            case "no_schedule":
                return "·";
            case "leave":
            case "holiday":
                return "—";
            default:
                return "✓";
        }
    }

    // --- punch actions ------------------------------------------------
    openPunch(punchId) {
        this.dialog.add(FormViewDialog, {
            resModel: "hr.attendance",
            resId: punchId,
            title: _t("Attendance"),
            onRecordSaved: () => this.load(),
        });
    }

    addPunch() {
        const { employeeId, cell } = this.state.selected;
        this.dialog.add(FormViewDialog, {
            resModel: "hr.attendance",
            title: _t("New Attendance"),
            context: {
                default_employee_id: employeeId,
                // The form expects a datetime; noon keeps it inside the day
                // whatever the timezone, the user sets the real hour.
                default_check_in: `${cell.date} 12:00:00`,
            },
            onRecordSaved: () => this.load(),
        });
    }

    async archivePunch(punchId) {
        // The locked-workday guard raises here; the error dialog is fine.
        await this.orm.write("hr.attendance", [punchId], { active: false });
        this.notification.add(_t("Attendance archived."), { type: "success" });
        await this.load();
    }

    // --- review -------------------------------------------------------
    async markReviewed(scope) {
        const { start, end } = this.range;
        let employeeIds;
        let dates;

        if (scope === "cell") {
            const { employeeId, cell } = this.state.selected;
            employeeIds = [employeeId];
            dates = [cell.date];
        } else {
            employeeIds = this.state.rows.map((r) => r.id);
            dates = this.state.dates.map((d) => d.date);
        }

        await this.orm.call("hr.workday", "review_mark", [employeeIds, dates]);
        this.notification.add(_t("Marked as reviewed."), { type: "success" });
        await this.load();
    }
}

export const attendanceReviewView = {
    type: "attendance_review",
    display_name: _t("Attendance Review"),
    icon: "fa fa-check-square-o",
    multiRecord: true,
    Controller: AttendanceReviewController,
    searchMenuTypes: ["filter", "groupBy", "favorite"],
};

// The "views" registry validates the type against session.view_info. On a
// session predating this module's install the key is missing and add()
// throws; the server (_get_view_info) is the real source, this only keeps
// the validation happy regardless of session freshness.
if (session.view_info && !session.view_info.attendance_review) {
    session.view_info.attendance_review = {
        display_name: "Attendance Review",
        icon: "fa fa-check-square-o",
        multi_record: true,
    };
}

registry.category("views").add("attendance_review", attendanceReviewView);
