<!-- Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
For license information, please see license.txt-->

# div_order_fulfillment

Design documents for every feature in `apps/div_order_fulfillment/`. Each feature is one `##` subsection
below; the structure inside each subsection is fixed (Summary, Problem, Target app, Functional
workflow, Schema, Overrides + hooks, Permissions, Out of scope, Open questions).

## Packing Slip Multi-Package Spread

### 1. Summary
A "Spread Across Packages" action on `Packing Slip` that distributes the slip's items evenly across N physical packages, preserves serial / batch bundle picks across the redistribution, and stamps each row with a `custom_package_no`. The slip's existing `from_case_no` / `to_case_no` range is reconciled at validate time so the package count, the row stamping, and the case range stay aligned.

### 2. Problem / why now
ERPNext's stock `Packing Slip` carries a `from_case_no` / `to_case_no` range but no per-row package attribution: when a slip ships across multiple cartons, operators have no way to record which item went into which case without splitting the slip. Hand-splitting is tedious, breaks the 1:1 link to the parent Delivery Note, and loses the serial/batch picks operators have already made. This feature lets a single slip span N packages, with row-level package numbers, even quantity distribution, and bundle-preserving redistribution when the operator changes the package count after picking.

### 3. Target app
App: `div_order_fulfillment`
Module: `DIV Order Fulfillment`

### 4. Functional workflow

1. **User** opens a draft Packing Slip with items and clicks **Spread Across Packages**. Trigger: form button (added by `packing_slip.js`).
2. **User** enters a target package count in the prompt and confirms. Trigger: dialog submit.
3. **System** — `div_order_fulfillment.api.packing_slip_spread.spread_across_packages(packing_slip, package_count, force=False)`:
   - Validates `package_count >= 1` and that the slip is a draft.
   - When existing rows already carry `custom_package_no` values that wouldn't survive the redistribution, returns `"needs_confirmation"` so the client can prompt the user before destructive redistribution.
   - When `force=True` (or no prior picks exist), divides the slip's items evenly across N packages, copying serial / batch picks where applicable. Existing `Serial and Batch Bundle` references are detached from the source DN (cloned at `docstatus = 0`) so each package's row carries its own bundle.
   - Sets `from_case_no = 1`, `to_case_no = N`, and stamps each row's `custom_package_no` with its target.
   Returns `"ok"` on success. Trigger: whitelisted method call.
4. **User (optional)** opens any Packing Slip Item row and clicks **Pick Serial / Batch No** to launch ERPNext's `SerialBatchPackageSelector`. The picker is wired by `packing_slip.js` and stores its selection in the row's `custom_serial_and_batch_bundle` Link. Trigger: row button.
5. **User** saves the slip. Trigger: standard form action.
6. **System** — `div_order_fulfillment.api.packing_slip_spread.validate(doc, method)` (registered via `Packing Slip → validate`) reconciles the row set against `from_case_no..to_case_no`, ensures each `custom_package_no` falls within the range and that every package in the range has at least one row, validates each row's serials are a subset of the parent DN's serial set, and refuses save when the same serial is claimed twice across sibling Packing Slips for the same DN. Trigger: `Packing Slip` `validate`.

### 5. Schema

#### 5.1 `Packing Slip` — Custom-Field

| Field name | Fieldtype | Options / Link target | Required | Notes |
|---|---|---|---|---|
| `custom_length` | Float | — | | Package length (one value for the whole slip; per-package dimensions live on individual rows when N > 1 is needed). |
| `custom_width` | Float | — | | Package width. |
| `custom_height` | Float | — | | Package height. |
| `custom_measurement_uom` | Link | UOM | | Default: `Inch`. UOM of the three dimension fields above. Surfaced in shipping CSV exports as `dim_uom`. |

#### 5.2 `Packing Slip Item` — Custom-Field

| Field name | Fieldtype | Options / Link target | Required | Notes |
|---|---|---|---|---|
| `custom_package_no` | Int | — | | Which physical package (1..N) this row belongs to. Visible in the list view. Stamped by `spread_across_packages`; reconciled at validate against `from_case_no..to_case_no`. |
| `custom_serial_no` | Section Break | — | | Section header for the serial / batch picker UI. |
| `custom_pick_serial__batch_no` | Button | — | | Form button that opens ERPNext's `SerialBatchPackageSelector` for this row. Wired in `packing_slip.js`. |
| `custom_serial_and_batch_bundle` | Link | Serial and Batch Bundle | | The bundle this row's serials/batches were picked into. Cloned (detached from the source DN) when the slip is spread so each package owns its own bundle. |

### 6. Overrides, hooks, and direct file edits

**`doc_events`:**
- `Packing Slip` → `validate` → `div_order_fulfillment.api.packing_slip_spread.validate` — reconciles `custom_package_no` against `from_case_no..to_case_no`, checks per-row serial subset and cross-slip uniqueness.

**`doctype_js`:**
- `Packing Slip` → `div_order_fulfillment/public/js/custom/packing_slip.js` — adds the **Spread Across Packages** form button and wires the row-level **Pick Serial / Batch No** button to ERPNext's `SerialBatchPackageSelector` (passes item, warehouse, posting_date / posting_time from the parent DN).

**File-direct edits** (within div_order_fulfillment):
- `apps/div_order_fulfillment/div_order_fulfillment/api/packing_slip_spread.py` — exposes `spread_across_packages(packing_slip, package_count, force)` (whitelisted) and `validate(doc, method)` (the doc-event handler).

### 7. Permissions

| Role | Read | Write | Create | Delete | Submit | Cancel |
|---|---|---|---|---|---|---|
| (inherits from `Packing Slip`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

The button and whitelisted method enforce standard Packing Slip write permission. Custom Fields inherit Packing Slip / Packing Slip Item permissions.

### 8. Out of scope

- **Per-row dimension overrides.** The four dimension fields are slip-level; if one package in a multi-package slip has different dimensions, the operator splits the slip into multiple packing slips manually.
- **Auto-redistribution on row qty changes.** The spread is one-shot at the time the operator clicks the button; later edits to row quantities don't auto-trigger redistribution.
- **Re-merging packages.** Once spread, the slip can be edited row-by-row but there's no "merge back into one package" button. Operators cancel + amend if they need to start over.

---

## Pluggable Shipping CSV Export

### 1. Summary
A pluggable CSV export framework on `Packing Slip`: operators select N slips in the list view, pick a configured `Shipping Export Adapter`, and download a per-package CSV whose columns are mapped through the adapter. The adapter's column rows pick from 44 neutral source fields (resolved at runtime against the slip + parent DN + Customer + Address + Contact), or fall back to a literal value. Each export persists a submitted `Shipping Export Log` and adds a back-linking comment to every contributing slip.

### 2. Problem / why now
Each shipping carrier accepts manifests in a different CSV shape — column order, header labels, weight units, and address-field naming all differ. Hard-coding one carrier's mapping into the slip-export path locks the bench to that carrier; rolling a new carrier shouldn't require code changes. The adapter doctype lets an operator describe a target carrier's CSV declaratively (label + source field, or label + literal), and the export engine resolves source values per package at runtime. The export log gives an audit trail that ties each generated CSV back to the contributing slips.

### 3. Target app
App: `div_order_fulfillment`
Module: `DIV Order Fulfillment`

### 4. Functional workflow

1. **Admin** creates a `Shipping Export Adapter` and adds `Shipping Export Adapter Column` rows. Each column has a `column_label` (the CSV header), and either a `source_field` (a Select from 44 supported neutral keys — see § 5.4) or a `literal_value` fallback. Trigger: standard form save.
2. **User** opens the Packing Slip list view, selects N slips, and clicks the **Export Shipping CSV** action (added by `packing_slip_list.js`). A picker prompts for an active adapter. Trigger: list-view button.
3. **System** — `div_order_fulfillment.api.shipping_export.export_shipping_csv(packing_slips, adapter)`:
   - Expands each slip into one CSV row per physical package (computed from `to_case_no - from_case_no + 1`, default 1).
   - Resolves each adapter column's value per row by walking the source-field key into the slip → parent DN → Customer → Customer Address (bill-to) / Shipping Address → Contact chain. Empty source values fall back to `literal_value`.
   - Renders the CSV body, encodes as base64, and inserts a submitted `Shipping Export Log` whose `packing_slips` table records each contributing slip's name and package count.
   - Adds a comment to each contributing slip linking back to the log.
   - Returns `{file_name, csv_base64, log_name, row_count}`. Trigger: whitelisted method call.
4. **User** — the client decodes `csv_base64`, triggers a browser download of `file_name`, and shows a success alert with the log name. Trigger: client-side handler in `packing_slip_list.js`.

### 5. Schema

#### 5.1 `Shipping Export Adapter` — New

| Field name | Fieldtype | Options / Link target | Required | Notes |
|---|---|---|---|---|
| `adapter_name` | Data | — | ✓ | Unique. Used as autoname. |
| `is_active` | Check | — | | Default: 1. Inactive adapters are hidden from the list-view picker but kept for history. |
| `columns` | Table | Shipping Export Adapter Column | ✓ | The CSV column mapping. Order in the table is the order in the CSV. |

#### 5.2 `Shipping Export Adapter Column` — New (child table)

Parent: `Shipping Export Adapter` via the `columns` Table field.

| Field name | Fieldtype | Options / Link target | Required | Notes |
|---|---|---|---|---|
| `column_label` | Data | — | ✓ | The literal header label written in the CSV. |
| `source_field` | Select | (44-option list — see § 5.4) | | When set, the export engine resolves this neutral key against the slip's source chain. When empty, `literal_value` wins. |
| `literal_value` | Data | — | | Fallback string when `source_field` is unset or resolves to an empty value. |

#### 5.3 `Shipping Export Log` — New

| Field name | Fieldtype | Options / Link target | Required | Notes |
|---|---|---|---|---|
| `naming_series` | Select | `SEL-.YYYY.-.#####` | ✓ | Submittable, append-only history. |
| `adapter` | Link | Shipping Export Adapter | ✓ | The adapter used for this run. |
| `user` | Link | User | ✓ | The operator who triggered the export. |
| `file_name` | Data | — | ✓ | The CSV filename returned to the client. |
| `row_count` | Int | — | | Total rows in the CSV (= sum of contributing slips' package counts). |
| `packing_slips` | Table | Shipping Export Log Item | | One row per contributing slip with its package count. |

#### 5.4 `Shipping Export Log Item` — New (child table)

Parent: `Shipping Export Log` via the `packing_slips` Table field.

| Field name | Fieldtype | Options / Link target | Required | Notes |
|---|---|---|---|---|
| `packing_slip` | Link | Packing Slip | ✓ | The contributing slip. |
| `package_count` | Int | — | | Number of CSV rows this slip produced (= `to_case_no - from_case_no + 1`). |

#### 5.5 Source-field key list (Select options on `Shipping Export Adapter Column.source_field`)

The 44 neutral keys exposed to adapters, resolved at runtime against the slip + parent DN + Customer + Address + Contact chain:

- **Package metadata:** `package_no`, `package_count`, `package_label`
- **Document IDs:** `packing_slip_name`, `delivery_note_name`
- **Weight / dimensions:** `gross_weight`, `weight_uom`, `length`, `width`, `height`, `dim_uom`
- **Customer:** `customer`, `customer_name`, `customer_contact_name`
- **Ship-to:** `shipping_address_name`, `shipping_address_line_1`, `shipping_address_line_2`, `shipping_address_city`, `shipping_address_state`, `shipping_address_pincode`, `shipping_address_country`, `shipping_contact_name`, `shipping_contact_phone`
- **Bill-to:** `customer_address_name`, `customer_address_line_1`, `customer_address_line_2`, `customer_address_city`, `customer_address_state`, `customer_address_pincode`, `customer_address_country`
- **Order:** `posting_date`, `po_no`, `sales_order`, `sales_order_po_no`, `project`, `contact_email`, `contact_mobile`
- **Notes / company:** `instructions`, `remarks`, `company`, `company_address`

### 6. Overrides, hooks, and direct file edits

**`doctype_js`:**
- `Shipping Export Adapter` → `div_order_fulfillment/public/js/custom/shipping_export_adapter.js` — adapter form helpers (column ordering, source-field documentation tooltips).

**`doctype_list_js`:**
- `Packing Slip` → `div_order_fulfillment/public/js/custom/packing_slip_list.js` — adds the **Export Shipping CSV** action; prompts for an active adapter; calls `export_shipping_csv`; downloads the returned base64 CSV.

**File-direct edits** (within div_order_fulfillment):
- `apps/div_order_fulfillment/div_order_fulfillment/api/shipping_export.py` — exposes `export_shipping_csv(packing_slips: list[str], adapter: str) -> dict` (whitelisted). Computes per-package rows, resolves source-field values, renders CSV, persists the `Shipping Export Log`, adds back-linking comments.
- `apps/div_order_fulfillment/div_order_fulfillment/div_order_fulfillment/doctype/shipping_export_adapter/`, `shipping_export_adapter_column/`, `shipping_export_log/`, `shipping_export_log_item/` — the four new doctypes.

### 7. Permissions

| Role | Read | Write | Create | Delete | Submit | Cancel |
|---|---|---|---|---|---|---|
| System Manager | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Sales Manager | ✓ | ✓ | ✓ | | ✓ | |
| Sales User | ✓ | | | | | |

(Applies to `Shipping Export Adapter`, `Shipping Export Log`. Child tables inherit from their parents.)

### 8. Out of scope

- **Direct API submission to carrier portals.** The adapter framework produces a CSV; operators upload it to the carrier's portal manually. A REST/SOAP integration per carrier is a follow-up.
- **Bundled rate shopping across carriers.** One adapter per export run; rate comparison across carriers is not in scope.
- **Pre-built adapter fixtures for common carriers.** The framework ships empty — operators configure their own adapters per tenant.
- **Automated shipping label generation.** The CSV is a manifest, not a label. Label printing (with tracking numbers) is downstream of carrier-portal upload.
- **Per-package weight overrides.** `gross_weight` resolves to the slip-level value; per-package weight differentiation isn't modeled. If needed it lives on a future `custom_package_weight` table on `Packing Slip Item`.
