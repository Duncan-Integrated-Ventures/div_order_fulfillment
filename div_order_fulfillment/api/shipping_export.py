# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

"""Build a shipping CSV for a set of Packing Slips, using a Shipping Export
Adapter to map neutral source fields to provider-specific column headers.

Fans each Packing Slip out into one row per physical package
(`from_case_no..to_case_no`), creates a submitted Shipping Export Log, and drops
a comment on each exported Packing Slip linking to the log."""

import base64
import csv
import io
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime


@frappe.whitelist()
def export_shipping_csv(packing_slips: list | str, adapter: str) -> dict:
	"""Return {filename, content_base64, log_name} for the client to trigger a
	browser download. Also creates a Shipping Export Log (submitted) and a
	comment on each exported Packing Slip."""
	if isinstance(packing_slips, str):
		packing_slips = frappe.parse_json(packing_slips)
	packing_slips = [p for p in (packing_slips or []) if p]
	if not packing_slips:
		frappe.throw(_("Select at least one Packing Slip."))

	adapter_doc = frappe.get_doc("Shipping Export Adapter", adapter)
	if not adapter_doc.is_active:
		frappe.throw(_("Shipping Export Adapter {0} is inactive.").format(adapter))
	if not adapter_doc.columns:
		frappe.throw(_("Adapter {0} has no columns configured.").format(adapter))

	rows = []
	per_ps_counts: dict[str, int] = {}
	for ps_name in packing_slips:
		ps_rows = expand_packing_slip(ps_name)
		per_ps_counts[ps_name] = len(ps_rows)
		rows.extend(ps_rows)

	csv_content = format_csv(rows, adapter_doc)
	filename = build_filename(adapter_doc.name)
	log = create_log(adapter_doc.name, filename, packing_slips, per_ps_counts, len(rows))
	add_comments(packing_slips, log.name, adapter_doc.name)

	return {
		"filename": filename,
		"content_base64": base64.b64encode(csv_content.encode("utf-8")).decode("ascii"),
		"log_name": log.name,
	}


# ---- Row expansion --------------------------------------------------------


def expand_packing_slip(ps_name: str) -> list[dict]:
	ps = frappe.get_doc("Packing Slip", ps_name)
	base = resolve_source_fields(ps)
	from_no = cint(ps.from_case_no) or 1
	to_no = cint(ps.to_case_no) or from_no
	if to_no < from_no:
		to_no = from_no
	package_count = to_no - from_no + 1
	out = []
	for pkg_no in range(from_no, to_no + 1):
		out.append(
			{
				**base,
				"package_no": pkg_no,
				"package_count": package_count,
				"package_label": f"{pkg_no} of {package_count}",
			}
		)
	return out


def resolve_source_fields(ps) -> dict:
	dn = (
		frappe.get_doc("Delivery Note", ps.delivery_note)
		if ps.delivery_note
		else frappe._dict()
	)
	ship_addr = address_fields(dn.get("shipping_address_name"))
	cust_addr = address_fields(dn.get("customer_address"))

	sales_order = None
	for item in dn.get("items") or []:
		so = item.get("against_sales_order")
		if so:
			sales_order = so
			break
	sales_order_po_no = (
		frappe.db.get_value("Sales Order", sales_order, "po_no") if sales_order else None
	)

	customer_contact_name = (
		frappe.db.get_value("Contact", dn.get("contact_person"), "full_name")
		if dn.get("contact_person")
		else None
	)

	return {
		"packing_slip_name": ps.name,
		"delivery_note_name": dn.get("name"),
		"gross_weight": flt(ps.gross_weight_pkg),
		"weight_uom": ps.gross_weight_uom,
		"length": flt(ps.get("custom_length")),
		"width": flt(ps.get("custom_width")),
		"height": flt(ps.get("custom_height")),
		"dim_uom": ps.get("custom_measurement_uom"),
		"customer": dn.get("customer"),
		"customer_name": dn.get("customer_name"),
		"customer_contact_name": customer_contact_name,
		"shipping_address_name": dn.get("shipping_address_name"),
		"shipping_address_line_1": ship_addr.get("address_line1"),
		"shipping_address_line_2": ship_addr.get("address_line2"),
		"shipping_address_city": ship_addr.get("city"),
		"shipping_address_state": ship_addr.get("state"),
		"shipping_address_pincode": ship_addr.get("pincode"),
		"shipping_address_country": ship_addr.get("country"),
		"shipping_contact_name": dn.get("contact_display") or dn.get("contact_person"),
		"shipping_contact_phone": dn.get("contact_mobile") or dn.get("contact_phone"),
		"customer_address_name": dn.get("customer_address"),
		"customer_address_line_1": cust_addr.get("address_line1"),
		"customer_address_line_2": cust_addr.get("address_line2"),
		"customer_address_city": cust_addr.get("city"),
		"customer_address_state": cust_addr.get("state"),
		"customer_address_pincode": cust_addr.get("pincode"),
		"customer_address_country": cust_addr.get("country"),
		"posting_date": dn.get("posting_date"),
		"po_no": dn.get("po_no"),
		"sales_order": sales_order,
		"sales_order_po_no": sales_order_po_no,
		"project": dn.get("project"),
		"contact_email": dn.get("contact_email"),
		"contact_mobile": dn.get("contact_mobile"),
		"instructions": dn.get("instructions"),
		"remarks": dn.get("remarks"),
		"company": dn.get("company"),
		"company_address": dn.get("company_address"),
	}


def address_fields(address_name: str | None) -> dict:
	if not address_name:
		return {}
	return (
		frappe.db.get_value(
			"Address",
			address_name,
			["address_line1", "address_line2", "city", "state", "pincode", "country"],
			as_dict=True,
		)
		or {}
	)


# ---- CSV formatting -------------------------------------------------------


def format_csv(rows: list[dict], adapter_doc) -> str:
	headers = [c.column_label or "" for c in adapter_doc.columns]
	buf = io.StringIO()
	writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
	writer.writerow(headers)
	for row in rows:
		out_row = []
		for col in adapter_doc.columns:
			if col.source_field:
				val = row.get(col.source_field)
				if val is None and col.literal_value:
					val = col.literal_value
			else:
				val = col.literal_value
			out_row.append("" if val is None else str(val))
		writer.writerow(out_row)
	return buf.getvalue()


# ---- Log + comments -------------------------------------------------------


def build_filename(adapter_name: str) -> str:
	slug = re.sub(r"[^a-z0-9]+", "-", adapter_name.lower()).strip("-")
	stamp = now_datetime().strftime("%Y%m%d-%H%M%S")
	return f"shipping-export-{slug}-{stamp}.csv"


def create_log(
	adapter: str,
	filename: str,
	packing_slips: list[str],
	counts: dict[str, int],
	row_count: int,
):
	log = frappe.new_doc("Shipping Export Log")
	log.adapter = adapter
	log.user = frappe.session.user
	log.file_name = filename
	log.row_count = row_count
	for ps_name in packing_slips:
		log.append(
			"packing_slips",
			{"packing_slip": ps_name, "package_count": counts.get(ps_name, 0)},
		)
	log.insert()
	log.submit()
	return log


def add_comments(packing_slips: list[str], log_name: str, adapter_name: str) -> None:
	link = f'<a href="/app/shipping-export-log/{log_name}">{log_name}</a>'
	content = _("Exported to shipping CSV via {0} ({1}).").format(
		link, frappe.utils.escape_html(adapter_name)
	)
	for ps_name in packing_slips:
		frappe.get_doc("Packing Slip", ps_name).add_comment("Info", content)
