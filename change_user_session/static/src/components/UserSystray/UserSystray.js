/** @odoo-module **/

import { Component,useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { registry } from "@web/core/registry";

/**
 * Represents the UserSystray component.
 */
export class UserSystray extends Component {
    /**
     * Sets up the component.
     */
    setup() {
        this.action = useService("action");
        this.user = useService("user");
        this.state = useState({ isAllowed: false });
        this.onWillStart(this.onWillStart);
    }

    /**
     * Initializes the component before it starts.
     */
    async onWillStart() {
        try {
            const isAllowed = await this.user.hasGroup('change_user_session.change_user_session_group_manager');
            this.state.isAllowed = isAllowed;
        } catch (error) {
            this.state.isAllowed = false;
        }
    }

    /**
     * Opens the change user wizard.
     */
    openChangeUserWizard(){
        this.action.doAction("change_user_session.action_change_user_session_wizard");
    }
}

UserSystray.template = "change_user_session.UserSystray";
export const systrayItem = {
    Component: UserSystray,
}

registry.category("systray").add("change_user_session.ChangeUser", systrayItem, { sequence: 1000 });