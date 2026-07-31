// Desk actions for the Super Admin on a Managed Company:
//  - Provision Site  : create the company's Frappe site end-to-end (Draft/Failed only)
//  - Push Subscription Now : push the current subscription snapshot to its site
// The primary console is the Flutter app; these mirror it for Desk users.
frappe.ui.form.on("Managed Company", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (["Draft", "Failed"].includes(frm.doc.provision_status)) {
			frm
				.add_custom_button(__("Provision Site"), () => provision_dialog(frm))
				.addClass("btn-primary");
		}

		frm.add_custom_button(__("Push Subscription Now"), () => {
			frappe.call({
				method: "asofi_saas.asofi_saas.subscription.push.push_now",
				args: { company: frm.doc.name },
				freeze: true,
				freeze_message: __("Pushing…"),
				callback(r) {
					if (r.exc || !r.message) return;
					const m = r.message;
					frappe.msgprint({
						title: m.ok ? __("Pushed") : __("Push Failed"),
						message: m.message,
						indicator: m.ok ? "green" : "red",
					});
					frm.reload_doc();
				},
			});
		});
	},
});

function provision_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Provision Site — {0}", [frm.doc.site_name]),
		fields: [
			{
				fieldtype: "HTML",
				options: `<p>${__(
					"A new Frappe site <b>{0}</b> will be created, utility_billing installed, and the manager user added.",
					[frappe.utils.escape_html(frm.doc.site_name)]
				)}</p>`,
			},
			{ fieldtype: "Data", fieldname: "manager_email", label: __("Manager Email"), reqd: 1 },
			{ fieldtype: "Password", fieldname: "manager_password", label: __("Manager Password"), reqd: 1 },
			{ fieldtype: "Column Break" },
			{ fieldtype: "Data", fieldname: "manager_first_name", label: __("Manager First Name") },
			{ fieldtype: "Data", fieldname: "manager_last_name", label: __("Manager Last Name") },
			{ fieldtype: "Section Break" },
			{
				fieldtype: "Password",
				fieldname: "admin_password",
				label: __("Site Admin Password (optional)"),
				description: __("Leave blank to use the default / a generated one."),
			},
		],
		primary_action_label: __("Create Site"),
		primary_action(values) {
			d.hide();
			frappe.call({
				method: "asofi_saas.asofi_saas.provisioning.provision.provision_existing",
				args: { company: frm.doc.name, ...values },
				freeze: true,
				freeze_message: __("Queuing…"),
				callback(r) {
					if (r.exc || !r.message) return;
					watch_progress(frm, r.message.operation_id);
				},
			});
		},
	});
	d.show();
}

function watch_progress(frm, operation_id) {
	const dlg = new frappe.ui.Dialog({
		title: __("Provisioning…"),
		fields: [{ fieldtype: "HTML", fieldname: "log" }],
	});
	dlg.show();
	const $log = dlg.fields_dict.log.$wrapper;
	let since = 0;
	let done = false;

	const color = (s) => (s === "error" ? "#c0392b" : s === "success" ? "#1e7e34" : "#444");
	const render = (events) => {
		events.forEach((e) => {
			$log.append(
				`<div style="font-family:monospace;font-size:12px;white-space:pre-wrap;color:${color(
					e.status
				)}">[${frappe.utils.escape_html(e.step)}] ${frappe.utils.escape_html(e.message)}</div>`
			);
		});
		const $body = dlg.$wrapper.find(".modal-body");
		$body.scrollTop($body.prop("scrollHeight"));
	};

	const poll = () => {
		if (done) return;
		frappe.call({
			method: "asofi_saas.asofi_saas.provisioning.provision.get_provision_progress",
			args: { operation_id, since },
			callback(r) {
				const m = r.message;
				if (m) {
					render(m.events);
					since = m.next;
					if (m.finished) {
						done = true;
						frm.reload_doc();
						frappe.show_alert({
							message: m.final_status === "SUCCESS" ? __("Site created") : __("Provisioning failed"),
							indicator: m.final_status === "SUCCESS" ? "green" : "red",
						});
						return;
					}
				}
				setTimeout(poll, 1500);
			},
		});
	};
	poll();
}
