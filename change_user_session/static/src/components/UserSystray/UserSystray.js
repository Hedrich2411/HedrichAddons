/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
import { registry } from "@web/core/registry";

export class UserSystray extends Component {
    static template = "change_user_session.UserSystray";
    static props = {};

    setup() {
        this.action = useService("action");
        this.state = useState({ isAllowed: false });
        onWillStart(async () => {
            try {
                this.state.isAllowed = await user.hasGroup(
                    "change_user_session.change_user_session_group_manager",
                );
            } catch {
                this.state.isAllowed = false;
            }
        });
    }

    openChangeUserWizard() {
        this.action.doAction("change_user_session.action_change_user_session_wizard");
    }
}

export const systrayItem = {
    Component: UserSystray,
};

registry.category("systray").add("change_user_session.ChangeUser", systrayItem, { sequence: 1000 });
