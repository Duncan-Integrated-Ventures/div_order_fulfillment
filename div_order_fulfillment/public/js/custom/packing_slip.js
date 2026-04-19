// Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
// For license information, please see license.txt

/* global div_order_fulfillment */

frappe.provide("div_order_fulfillment");

frappe.ui.form.on("Packing Slip", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.docstatus !== 0) return;
		frm.add_custom_button(__("Spread Across Packages"), () =>
			div_order_fulfillment.prompt_spread(frm),
		);
	},
});

div_order_fulfillment.prompt_spread = (frm) => {
	frappe.prompt(
		[
			{
				fieldtype: "Int",
				fieldname: "package_count",
				label: __("Package Count"),
				default: Math.max(
					1,
					(frm.doc.to_case_no || frm.doc.from_case_no || 1) - (frm.doc.from_case_no || 1) + 1,
				),
				reqd: 1,
			},
		],
		({ package_count }) => div_order_fulfillment.run_spread(frm, package_count, false),
		__("Spread Across Packages"),
		__("Spread"),
	);
};

div_order_fulfillment.run_spread = (frm, package_count, force) => {
	frappe
		.call({
			method: "div_order_fulfillment.api.packing_slip_spread.spread_across_packages",
			args: {
				packing_slip: frm.doc.name,
				package_count,
				force: force ? 1 : 0,
			},
			freeze: true,
			freeze_message: __("Spreading rows…"),
		})
		.then((r) => {
			const res = r.message || {};
			if (res.status === "needs_confirmation") {
				frappe.confirm(
					__(
						"Rows are already spread. Re-spreading will rebuild the rows and discard existing serial/batch bundle picks. Continue?",
					),
					() => div_order_fulfillment.run_spread(frm, package_count, true),
				);
				return;
			}
			frm.reload_doc().then(() =>
				frappe.show_alert({
					message: __("Spread across {0} package(s).", [package_count]),
					indicator: "green",
				}),
			);
		});
};

frappe.ui.form.on("Packing Slip Item", {
	custom_pick_serial__batch_no(frm, cdt, cdn) {
		const item = locals[cdt][cdn];
		if (!item.item_code) {
			frappe.msgprint(__("Set item first."));
			return;
		}
		// The SABB picker reads `item.warehouse` for its default and filter;
		// its server call also uses parent_doc.posting_date / posting_time.
		// Packing Slip has none of these, so borrow from DN + DN Item.
		// frappe.db.get_value doesn't work for child doctypes — route the
		// DN Item lookup through div_frappe_base.utils.get_child_item.
		const dn_item_call = item.dn_detail
			? frappe.call({
					method: "div_frappe_base.utils.get_child_item",
					args: {
						doctype: "Delivery Note Item",
						name: item.dn_detail,
						fieldname: "warehouse",
					},
			  })
			: Promise.resolve({ message: null });
		Promise.all([
			frappe.db.get_value("Item", item.item_code, ["has_batch_no", "has_serial_no"]),
			frappe.db.get_value("Delivery Note", frm.doc.delivery_note, [
				"set_warehouse",
				"posting_date",
				"posting_time",
			]),
			dn_item_call,
		]).then(([itemR, dnR, dniR]) => {
			item.has_batch_no = itemR.message.has_batch_no;
			item.has_serial_no = itemR.message.has_serial_no;
			item.type_of_transaction = "Outward";
			// get_child_item returns the raw value when fieldname is a string,
			// so dniR.message is the warehouse string (or null) directly.
			const wh = dniR.message || dnR.message.set_warehouse;
			if (wh) item.warehouse = wh;
			frm.doc.posting_date = dnR.message.posting_date || frappe.datetime.get_today();
			frm.doc.posting_time = dnR.message.posting_time || frappe.datetime.now_time();
			new erpnext.SerialBatchPackageSelector(frm, item, (result) => {
				if (!result) return;
				frappe.model.set_value(cdt, cdn, {
					custom_serial_and_batch_bundle: result.name,
					qty: Math.abs(result.total_qty),
				});
			});
		});
	},
});
