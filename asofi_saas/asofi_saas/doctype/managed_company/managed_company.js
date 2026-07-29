// Desk convenience for the Super Admin: push this company's subscription to its
// site on demand (the primary console is the Flutter app; this is for setup/debug).
frappe.ui.form.on("Managed Company", {
	refresh(frm) {
		if (frm.is_new()) return;
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
