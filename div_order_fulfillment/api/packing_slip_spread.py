# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

"""Spread a Packing Slip's items across multiple physical packages, evenly
splitting qty and serial/batch bundles. Validates that serial selections are a
subset of the parent Delivery Note's serials and that no serial is claimed twice
across any Packing Slip for the same DN.

Parent `from_case_no`/`to_case_no` is the master: changing `to_case_no` on save
auto-spreads. The explicit "Spread Across Packages" button forces a re-spread."""

import math
from collections import OrderedDict, defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt


# ---- Public whitelisted method --------------------------------------------


@frappe.whitelist()
def spread_across_packages(
	packing_slip: str, package_count: int | str, force: bool | int = False
) -> dict:
	"""Fan out each Packing Slip Item row into `package_count` sibling rows
	(package_no = from_case_no..from_case_no+N-1), splitting qty and serials
	evenly. First N-1 rows get ceil(qty/N); the last row absorbs the remainder.

	If `force` is falsy and any row already carries a `custom_package_no >
	from_case_no` (meaning a previous spread exists), returns a prompt flag so
	the client can confirm overwrite."""
	package_count = cint(package_count)
	if package_count < 1:
		frappe.throw(_("Package count must be at least 1."))

	ps = frappe.get_doc("Packing Slip", packing_slip)
	from_no = cint(ps.from_case_no) or 1
	ps.from_case_no = from_no
	ps.to_case_no = from_no + package_count - 1

	# Warn when existing picks would get re-distributed so the user is explicit
	# about it. The data transformation is deterministic (serials are preserved,
	# just re-split across the new box count), but the per-box assignment changes.
	if has_picked_bundles(ps) and not cint(force):
		return {"status": "needs_confirmation"}

	reconcile_rows(ps, from_no, package_count)
	ps.save()
	return {"status": "ok", "name": ps.name}


# ---- Document hooks -------------------------------------------------------


def validate(doc, method=None):
	"""Applied via hooks.py as Packing Slip's validate hook. Keeps the row
	distribution in sync with from_case_no..to_case_no: spreads when the range
	widens, consolidates when it narrows, and leaves rows alone when the range
	already matches (so manually picked serial bundles aren't wiped)."""
	from_no = cint(doc.from_case_no) or 1
	to_no = cint(doc.to_case_no) or from_no
	if to_no < from_no:
		to_no = from_no

	for row in doc.items:
		if not cint(row.get("custom_package_no") or 0):
			row.custom_package_no = from_no

	package_count = to_no - from_no + 1
	if needs_reconcile(doc, from_no, package_count):
		reconcile_rows(doc, from_no, package_count)

	validate_serial_subsets_and_overlap(doc)


# ---- Spread mechanics -----------------------------------------------------


def has_picked_bundles(doc) -> bool:
	return any(r.get("custom_serial_and_batch_bundle") for r in doc.items)


def needs_reconcile(doc, from_no: int, package_count: int) -> bool:
	"""Return True when the current row layout doesn't match the expected set of
	package_nos (from_no..from_no+package_count-1). Each source — identified by
	(dn_detail, pi_detail, item_code) — is expected to have exactly one row per
	package_no in the range."""
	expected = set(range(from_no, from_no + package_count))
	by_source: dict[tuple, list[int]] = defaultdict(list)
	for row in doc.items:
		key = (
			row.get("dn_detail") or "",
			row.get("pi_detail") or "",
			row.get("item_code") or "",
		)
		by_source[key].append(cint(row.get("custom_package_no") or 0))
	for pkgs in by_source.values():
		if set(pkgs) != expected or len(pkgs) != package_count:
			return True
	return False


def reconcile_rows(doc, from_no: int, package_count: int) -> None:
	"""Collapse sibling rows back into one source per (dn_detail, pi_detail,
	item_code), then fan out across `package_count` packages. Works in both
	directions: expanding the range splits serials, narrowing it re-merges them
	into fewer rows."""
	sources = collect_sources(doc.items)
	doc.set("items", [])
	for src in sources:
		fan_out_source(doc, src, from_no, package_count)


def collect_sources(rows) -> list[dict]:
	"""Group spread siblings back into single source rows. For each unique
	(dn_detail, pi_detail, item_code) tuple, sum the qty across all rows and
	concatenate every row's bundle serials in row order. The first row's other
	fields become the template."""
	groups: OrderedDict[tuple, dict] = OrderedDict()
	for row in rows:
		key = (
			row.get("dn_detail") or "",
			row.get("pi_detail") or "",
			row.get("item_code") or "",
		)
		row_serials = bundle_serials(row.get("custom_serial_and_batch_bundle"))
		src = groups.get(key)
		if src is None:
			src = row.as_dict()
			src["qty"] = flt(row.qty)
			src["_serials"] = list(row_serials)
			# Keep one existing bundle as a metadata template (item_code,
			# warehouse, company, has_serial_no, etc.) for the clones we'll mint.
			src["_template_bundle"] = row.get("custom_serial_and_batch_bundle") or None
			groups[key] = src
		else:
			src["qty"] = flt(src["qty"]) + flt(row.qty)
			src["_serials"].extend(row_serials)
			if not src["_template_bundle"]:
				src["_template_bundle"] = row.get("custom_serial_and_batch_bundle") or None
	return list(groups.values())


def fan_out_source(doc, src: dict, from_no: int, n: int) -> None:
	total_qty = flt(src.get("qty"))
	splits = split_counts(total_qty, n)
	serials = src.get("_serials") or []
	slices = slice_serials(serials, splits)

	# Fields that must not copy onto new child rows.
	drop = {
		"name",
		"idx",
		"creation",
		"modified",
		"modified_by",
		"owner",
		"docstatus",
		"parent",
		"parenttype",
		"parentfield",
		"custom_serial_and_batch_bundle",
		"_serials",
		"_template_bundle",
	}
	template = {k: v for k, v in src.items() if k not in drop}

	for i, qty in enumerate(splits):
		new_row = doc.append("items", template)
		new_row.qty = qty
		new_row.custom_package_no = from_no + i
		if slices[i]:
			new_row.custom_serial_and_batch_bundle = clone_bundle_with_serials(
				src.get("_template_bundle"), slices[i], qty
			)
		else:
			new_row.custom_serial_and_batch_bundle = None


def split_counts(total: float, n: int) -> list[float]:
	if n <= 1:
		return [total]
	base = math.ceil(total / n)
	return [float(base)] * (n - 1) + [float(total - base * (n - 1))]


def slice_serials(serials: list[str], splits: list[float]) -> list[list[str]]:
	out: list[list[str]] = []
	cursor = 0
	for qty in splits:
		take = int(qty)
		out.append(serials[cursor : cursor + take])
		cursor += take
	return out


# ---- Bundle helpers -------------------------------------------------------


def bundle_serials(bundle_name: str | None) -> list[str]:
	if not bundle_name or not frappe.db.exists("Serial and Batch Bundle", bundle_name):
		return []
	entries = frappe.get_all(
		"Serial and Batch Entry",
		filters={"parent": bundle_name},
		fields=["serial_no"],
		order_by="idx",
	)
	return [e.serial_no for e in entries if e.serial_no]


def clone_bundle_with_serials(
	source_bundle: str | None, serials: list[str], qty: float
) -> str | None:
	"""Create a new Serial and Batch Bundle holding the given serials. Copies
	item_code/warehouse/type_of_transaction from the source bundle, detaches
	voucher references (the new bundle belongs to the Packing Slip row, not to
	the source DN), and leaves docstatus=0 — Packing Slips don't post SLEs, so
	the bundle is purely an informational serial list."""
	if not serials:
		return None
	src = (
		frappe.get_doc("Serial and Batch Bundle", source_bundle)
		if source_bundle and frappe.db.exists("Serial and Batch Bundle", source_bundle)
		else None
	)
	new = frappe.new_doc("Serial and Batch Bundle")
	if src:
		new.item_code = src.item_code
		new.warehouse = src.warehouse
		new.has_serial_no = src.has_serial_no
		new.has_batch_no = src.has_batch_no
		new.type_of_transaction = src.type_of_transaction or "Outward"
		new.company = src.company
	else:
		new.type_of_transaction = "Outward"
	new.total_qty = qty
	for sn in serials:
		new.append("entries", {"serial_no": sn, "qty": 1})
	new.flags.ignore_validate = True
	new.insert(ignore_permissions=True)
	return new.name


# ---- Validation -----------------------------------------------------------


def validate_serial_subsets_and_overlap(doc) -> None:
	dn = doc.delivery_note
	sibling_serials = collect_sibling_serials(dn, exclude_ps=doc.name or "")
	dn_serials_cache: dict[str, set[str]] = {}
	on_this_doc: dict[str, str] = {}

	for row in doc.items:
		bundle = row.get("custom_serial_and_batch_bundle")
		if not bundle:
			continue
		serials = bundle_serials(bundle)

		if serials and len(serials) != int(flt(row.qty)):
			frappe.throw(
				_("Row {0}: bundle has {1} serial(s) but row qty is {2}.").format(
					row.idx, len(serials), int(flt(row.qty))
				)
			)

		dn_item = row.get("dn_detail")
		if dn_item and serials:
			if dn_item not in dn_serials_cache:
				dn_bundle = frappe.db.get_value(
					"Delivery Note Item", dn_item, "serial_and_batch_bundle"
				)
				dn_serials_cache[dn_item] = set(bundle_serials(dn_bundle))
			dn_set = dn_serials_cache[dn_item]
			if dn_set:
				out_of_set = [s for s in serials if s not in dn_set]
				if out_of_set:
					frappe.throw(
						_("Row {0}: serials {1} are not on the Delivery Note Item.").format(
							row.idx, ", ".join(out_of_set[:5])
						)
					)

		for sn in serials:
			if sn in on_this_doc:
				frappe.throw(
					_("Serial {0} appears on rows {1} and {2}.").format(sn, on_this_doc[sn], row.idx)
				)
			if sn in sibling_serials:
				frappe.throw(
					_("Serial {0} is already claimed on {1}.").format(sn, sibling_serials[sn])
				)
			on_this_doc[sn] = str(row.idx)


def collect_sibling_serials(dn: str, exclude_ps: str) -> dict[str, str]:
	"""Return {serial_no: 'Packing Slip X pkg N'} for every serial claimed on
	any non-cancelled Packing Slip for `dn` other than `exclude_ps`."""
	if not dn:
		return {}
	sibling_ps = frappe.get_all(
		"Packing Slip",
		filters={
			"delivery_note": dn,
			"docstatus": ("<", 2),
			"name": ("!=", exclude_ps or "__nomatch__"),
		},
		pluck="name",
	)
	if not sibling_ps:
		return {}
	out: dict[str, str] = {}
	rows = frappe.get_all(
		"Packing Slip Item",
		filters={
			"parent": ("in", sibling_ps),
			"custom_serial_and_batch_bundle": ("is", "set"),
		},
		fields=["parent", "custom_serial_and_batch_bundle", "custom_package_no"],
	)
	for r in rows:
		for sn in bundle_serials(r.custom_serial_and_batch_bundle):
			out[sn] = f"{r.parent} pkg {r.custom_package_no}"
	return out
