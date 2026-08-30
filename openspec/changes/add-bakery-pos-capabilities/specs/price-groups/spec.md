## Purpose

Lets an operator set one price level for many items and apply it across many companies/outlets at once, instead of editing `Item Price` row by row, and have POS use those prices at the correct outlet.

## ADDED Requirements

### Requirement: Price group definition

The system SHALL provide a price group record holding a name, a currency, an optional price list, an enabled flag, a list of per-item rates, and a list of outlets it applies to. Each item rate SHALL identify an item, an optional unit of measure, and a rate.

#### Scenario: Rates set in one place

- **WHEN** an operator enters rates for a hundred items on one price group
- **THEN** no individual `Item Price` record needs to be created for that price level

#### Scenario: Disabled group is inert

- **WHEN** a price group is disabled and its outlet is served
- **THEN** the group's rates are not applied

### Requirement: Outlet-scoped application to many companies

The system SHALL let one price group list multiple outlets, each identified by company, warehouse, and optionally a POS Profile, and SHALL apply the group only at the outlets it lists.

#### Scenario: One master, many companies

- **WHEN** a price group lists outlets in two different companies
- **THEN** the same group's rates apply in both companies at submission time

#### Scenario: Outlet not listed keeps standard pricing

- **WHEN** an item is sold at an outlet that the price group does not list
- **THEN** the standard price list rate applies, not the group rate

#### Scenario: Ambiguous outlet assignment

- **WHEN** two enabled price groups claim the same outlet for the same item
- **THEN** resolution fails with an actionable configuration error naming both groups, rather than picking one at random

### Requirement: Price applied at the point of sale

The system SHALL resolve an item's selling rate through POS Next's own pricing path using the outlet's price group when one applies, and SHALL fall back to the standard price list when no price group covers the item.

#### Scenario: Group overrides the price list

- **WHEN** an outlet has a price group and an item is on that group at 8,000 while the price list says 10,000
- **THEN** the cart and the submitted transaction use 8,000

#### Scenario: Item absent from a present group

- **WHEN** an outlet has a price group but the sold item is not listed on it
- **THEN** the standard price list rate applies to that item

#### Scenario: Rate respects unit of measure

- **WHEN** a price group lists a per-piece rate for an item and the cashier sells per box
- **THEN** the conversion follows the item's unit-of-measure settings

### Requirement: Price group editing is safe under concurrency

The system SHALL apply edits to a price group's items and outlets so that two operators editing the same group concurrently do not silently overwrite each other's unrelated changes.

#### Scenario: Concurrent edits preserved

- **WHEN** two operators change different items on the same price group at nearly the same time
- **THEN** both changes are reflected and neither is lost

### Requirement: Feature is opt-in per profile

The system SHALL behave exactly as today when the price-group feature is disabled for the profile, ignoring any defined group and using standard pricing.

#### Scenario: Disabled profile unchanged

- **WHEN** price groups are disabled for a POS profile
- **THEN** no outlet price group is consulted and rates come from the price list as before

### Requirement: Offline and reporting parity

The system SHALL cache the applicable price group for an outlet so a sale priced offline matches what an online submission would compute, and SHALL record which price group produced each transaction line's rate.

#### Scenario: Offline price matches online

- **WHEN** a sale is priced offline from a cached price group and later synced
- **THEN** the server recomputes the same rate and does not silently change it

#### Scenario: Line shows its price source

- **WHEN** a transaction line is priced by a price group
- **THEN** the line records the price group so reports can attribute the margin to that price level
