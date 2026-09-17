# Demo import contracts

Master imports use generated templates and explicit persisted MDM relationships.
Customer workbooks use the Customers sheet. SKU and combined goods imports bind
to Products explicitly; deterministic code normalization does not allow fuzzy
business-entity guesses. No entity exception lists are bundled.

Sales uses a documented demo ERP adapter, preserving signed raw quantities and
mapping outcomes. Published quantity facts contain eligible positive rows;
KIT_PARENT identifies a demo kit-parent row. The decline confirmation threshold
is configurable, and source/month publication is atomic.

Inventory uses a fourth-row header demo workbook. Unknown warehouses fail
validation. Planning availability is explicit and versioned. Signed movements
are retained; inventory issues do not substitute for sales quantities.

Incoming uses generated shipment workbooks, explicit SKU resolution and
versioned snapshots. Forecasts are manually supplied immutable versions. Final
Orders are manually supplied quantities with pre-change revisions. Four-month
projection is Opening + Incoming - Forecast; blank quantities are not zero.
