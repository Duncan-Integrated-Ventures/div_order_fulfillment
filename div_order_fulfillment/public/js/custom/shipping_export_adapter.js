// Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
// For license information, please see license.txt

// Reference table injected above the column mapping table so users can see
// every source_field key and its meaning without scrolling a cramped text
// field. Data lives here (not in the doctype) so keeping it in sync with the
// server-side row builder is a single-file change.

const SOURCE_FIELDS = [
	[
		"package_no",
		"Physical package number; export emits one row per value in from_case_no..to_case_no.",
	],
	["package_count", "Total packages on the slip (to_case_no - from_case_no + 1)."],
	[
		"package_label",
		'Human-readable "X of Y" (e.g. "1 of 2", "3 of 9") combining package_no and package_count.',
	],
	["packing_slip_name", "ERPNext Packing Slip ID (e.g. MAT-PAC-2026-00001)."],
	["delivery_note_name", "Linked Delivery Note ID."],
	["gross_weight", "Gross weight per package from the Packing Slip's gross_weight_pkg."],
	["weight_uom", "Unit for gross weight (gross_weight_uom)."],
	["length", "Package length (custom_length)."],
	["width", "Package width (custom_width)."],
	["height", "Package height (custom_height)."],
	["dim_uom", "Dimensions unit (custom_measurement_uom)."],
	["customer", "Customer ID from the Delivery Note."],
	["customer_name", "Customer display name (typically the company / organization)."],
	[
		"customer_contact_name",
		"Individual contact person's full name — DN contact_person → Contact.full_name. Use this for the name line above the company on a shipping label.",
	],
	["shipping_address_name", "Address doc name used as ship-to on the DN."],
	["shipping_address_line_1", "Ship-to address line 1."],
	["shipping_address_line_2", "Ship-to address line 2."],
	["shipping_address_city", "Ship-to city."],
	["shipping_address_state", "Ship-to state / province."],
	["shipping_address_pincode", "Ship-to ZIP / postal code."],
	["shipping_address_country", "Ship-to country."],
	["shipping_contact_name", "Shipping contact on the DN (falls back to contact_person)."],
	["shipping_contact_phone", "Shipping contact mobile (falls back to phone)."],
	["customer_address_name", "Customer's billing address doc name (customer_address)."],
	["customer_address_line_1", "Billing address line 1."],
	["customer_address_line_2", "Billing address line 2."],
	["customer_address_city", "Billing city."],
	["customer_address_state", "Billing state / province."],
	["customer_address_pincode", "Billing ZIP / postal code."],
	["customer_address_country", "Billing country."],
	["posting_date", "Delivery Note's posting date."],
	["po_no", "Customer PO number on the Delivery Note."],
	["sales_order", "First Sales Order linked via a DN Item (against_sales_order)."],
	["sales_order_po_no", "Customer PO on that Sales Order."],
	["project", "Project on the DN."],
	["contact_email", "Contact email on the DN."],
	["contact_mobile", "Contact mobile on the DN."],
	["instructions", "DN instructions — order-level shipping notes, useful for Notes columns."],
	["remarks", "DN remarks."],
	["company", "Company on the DN."],
	["company_address", "DN company_address (return-from address ID)."],
];

frappe.ui.form.on("Shipping Export Adapter", {
	refresh(frm) {
		render_reference_table(frm);
	},
});

function render_reference_table(frm) {
	const $columns = frm.fields_dict.columns && frm.fields_dict.columns.$wrapper;
	if (!$columns) return;
	// Idempotent: remove any prior injection before re-rendering on refresh.
	$columns.siblings(".source-field-reference").remove();

	const esc = frappe.utils.escape_html;
	const rows = SOURCE_FIELDS.map(
		([k, d]) => `<tr><td><code>${esc(k)}</code></td><td>${esc(d)}</td></tr>`,
	).join("");

	const $block = $(`
		<div class="source-field-reference" style="margin: 10px 0 20px;">
			<div style="display: flex; align-items: center; gap: 8px; cursor: pointer;" class="source-field-reference-toggle">
				<span class="octicon octicon-chevron-down" style="font-size: 11px;"></span>
				<b>${__("Source Field Reference")}</b>
				<span class="text-muted" style="font-size: 12px;">
					${__("Neutral keys available to the <code>source_field</code> column below.")}
				</span>
			</div>
			<div class="source-field-reference-body" style="margin-top: 8px; display: none;">
				<table class="table table-sm" style="font-size: 12px; margin-bottom: 0;">
					<thead><tr>
						<th style="width: 240px;">${__("Source Field")}</th>
						<th>${__("Description")}</th>
					</tr></thead>
					<tbody>${rows}</tbody>
				</table>
			</div>
		</div>
	`);

	$block.find(".source-field-reference-toggle").on("click", () => {
		$block.find(".source-field-reference-body").toggle();
	});

	$columns.before($block);
}
