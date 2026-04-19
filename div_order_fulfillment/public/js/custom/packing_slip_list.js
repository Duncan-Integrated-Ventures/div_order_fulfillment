// Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
// For license information, please see license.txt

frappe.listview_settings["Packing Slip"] = {
	onload(listview) {
		listview.page.add_action_item(__("Export Shipping CSV"), () => {
			const names = listview.get_checked_items().map((d) => d.name);
			if (!names.length) {
				frappe.msgprint(__("Select at least one Packing Slip."));
				return;
			}
			frappe.prompt(
				[
					{
						fieldtype: "Link",
						fieldname: "adapter",
						label: __("Shipping Export Adapter"),
						options: "Shipping Export Adapter",
						reqd: 1,
						get_query: () => ({ filters: { is_active: 1 } }),
					},
				],
				({ adapter }) => run_export(names, adapter),
				__("Export Shipping CSV"),
				__("Export"),
			);
		});
	},
};

function run_export(names, adapter) {
	frappe
		.call({
			method: "div_order_fulfillment.api.shipping_export.export_shipping_csv",
			args: { packing_slips: names, adapter },
			freeze: true,
			freeze_message: __("Building CSV…"),
		})
		.then((r) => {
			const res = r.message || {};
			if (!res.content_base64) return;
			download_csv(res.filename, res.content_base64);
			frappe.show_alert({
				message: __("Exported {0} — log {1}", [res.filename, res.log_name]),
				indicator: "green",
			});
		});
}

function download_csv(filename, base64_content) {
	// Decode base64 → Blob → trigger browser download without a round-trip
	// through a download endpoint.
	const binary = atob(base64_content);
	const bytes = new Uint8Array(binary.length);
	for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
	const blob = new Blob([bytes], { type: "text/csv;charset=utf-8" });
	const url = URL.createObjectURL(blob);
	const a = document.createElement("a");
	a.href = url;
	a.download = filename;
	document.body.appendChild(a);
	a.click();
	document.body.removeChild(a);
	URL.revokeObjectURL(url);
}
