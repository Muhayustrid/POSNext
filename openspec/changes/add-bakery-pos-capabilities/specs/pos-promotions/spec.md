## Purpose

Lets a bakery sell a combo product — one priced parent item that expands into mandatory components plus a constrained choice of items — with correct stock, pricing, and receipt behaviour. The model is adopted from the proven `selling_additional` Promotion feature and ported into POS Next.

## ADDED Requirements

### Requirement: Promotion definition

The system SHALL provide a Promotion record that maps a sellable parent item to a base price and a set of components. A promotion SHALL belong to one company, SHALL be enableable and disableable, SHALL carry optional validity dates, and SHALL define which outlets (company + warehouse) may sell it. The buyer pays the parent item's base price plus any chosen option adjustments, not the sum of individual item prices.

#### Scenario: Priced below its parts

- **WHEN** a promotion's components are worth 25,000 and its base price is 20,000 with no adjustments
- **THEN** selling it charges 20,000
- **AND** no component contributes its own standalone price to the cart total

#### Scenario: Outlet not permitted

- **WHEN** a cashier sells a promotion from a warehouse not listed on the promotion
- **THEN** the promotion is not offered for that outlet
- **AND** submitting a sale that references it is rejected

#### Scenario: Expired promotion cannot be sold

- **WHEN** a promotion's validity window has passed and a cashier tries to add it
- **THEN** the promotion is not offered and a submitted reference is rejected

### Requirement: Mandatory components

The system SHALL let a promotion declare fixed components that are always included, each with a quantity. Fixed components SHALL NOT be removable or adjustable by the cashier.

#### Scenario: Paperbag always included

- **WHEN** a cashier completes a promotion selection for a promotion whose fixed component is one paperbag
- **THEN** the resulting transaction includes one paperbag at quantity 1
- **AND** the cashier had no control to remove or change it

#### Scenario: Fixed component short on stock blocks the sale

- **WHEN** a promotion's fixed component is out of stock at the transaction warehouse
- **THEN** submitting the sale is rejected with that component named as the shortage

### Requirement: Choice groups

The system SHALL let a promotion declare one or more choice groups. Each group SHALL define its selectable options, a number of picks (`pick_count`), a per-option maximum (`max_per_option`), and whether the same option may be repeated. A group with `pick_count` 1 SHALL behave as a single choice.

#### Scenario: Single choice enforced

- **WHEN** a group offers ropi coklat, ropi keju, and ropi butter with `pick_count` 1
- **THEN** the cashier can confirm exactly one
- **AND** a selection with two is rejected

#### Scenario: Multiple distinct choices allowed

- **WHEN** a group has `pick_count` 3 and each option's `max_per_option` is 1
- **THEN** a selection of three different flavours is accepted
- **AND** repeating one flavour is rejected because it exceeds `max_per_option`

#### Scenario: Repeats allowed when configured

- **WHEN** a group has `pick_count` 3 with repeats allowed and `max_per_option` 3
- **THEN** the cashier can select the same flavour three times, consuming three units of that component

#### Scenario: Under-picked group rejected

- **WHEN** a group with `pick_count` 2 is confirmed with only one selection
- **THEN** the cart refuses the promotion and names the group that is short

### Requirement: Selection at the point of sale

The system SHALL require a valid selection for every group before a promotion enters the cart, and SHALL let the cashier edit that selection afterwards from the cart line. The quantity of a promotion line SHALL scale its components proportionally.

#### Scenario: Dialog interrupts add-to-cart

- **WHEN** a cashier taps a promotion item in the catalogue
- **THEN** a selection dialog listing the promotion's groups appears before the item is placed in the cart

#### Scenario: Editing a selection in place

- **WHEN** a cashier opens an existing promotion line and changes a flavour
- **THEN** the transaction's selection is replaced to match
- **AND** the promotion line's base price and quantity are unchanged

#### Scenario: Quantity scales components

- **WHEN** a promotion line is set to quantity 2 and its selection includes two of one flavour
- **THEN** that flavour consumes four units of stock

#### Scenario: Instance limit per invoice

- **WHEN** a promotion has `max_instances_per_invoice` 2 and the cart already holds three of it
- **THEN** the third is rejected with the limit stated

### Requirement: Server-side validation of selections

The system SHALL re-validate every promotion selection against the promotion definition when the transaction is created or submitted, independently of the client. A selection that violates a pick count, exceeds a per-option maximum, references an item outside its group, or references a non-existent promotion SHALL be rejected.

#### Scenario: Client-side constraint bypass rejected

- **WHEN** a request submits a promotion line whose selection exceeds the group pick count
- **THEN** the transaction is rejected with the violated constraint identified

#### Scenario: Foreign option rejected

- **WHEN** a submitted selection includes an item that is not an option of the group it is filed under
- **THEN** the transaction is rejected

### Requirement: Stock and accounting effects

The system SHALL deduct stock for each component item at its selected quantity and SHALL NOT deduct stock for the parent promotion item. Revenue SHALL be recognised on the promotion line at the base price plus adjustments.

#### Scenario: Components are debited, parent is not

- **WHEN** a promotion containing one paperbag and one flavoured item is submitted with stock updates enabled
- **THEN** stock ledger entries exist for the paperbag and the flavoured item
- **AND** no stock ledger entry exists for the parent promotion item

### Requirement: Selection is snapshotted and queryable

The system SHALL record, per promotion instance on a transaction, an immutable snapshot of what was selected and its total, and SHALL additionally record a queryable fact row per chosen item identifying whether it was a fixed component or a chosen option and its adjustment.

#### Scenario: Reprint reproduces the selection

- **WHEN** a transaction containing a promotion is reprinted later
- **THEN** the printed selection equals the snapshot taken at sale time, unaffected by later edits to the promotion definition

#### Scenario: Reporting reads the facts

- **WHEN** an analyst asks which flavours sold through promotions in a period
- **THEN** the selection facts answer it without parsing transaction payloads

### Requirement: Receipt presentation

The system SHALL present a promotion as one priced line to the buyer on the printed receipt by default, with its components shown as indented unpriced detail beneath it, and SHALL let the cashier expand the full component view.

#### Scenario: Collapsed on receipt

- **WHEN** a receipt prints for a transaction containing a promotion with a two-flavour selection
- **THEN** the promotion appears as one priced line
- **AND** the chosen items and quantities appear beneath it as unpriced detail

### Requirement: Returns of promotions

The system SHALL allow returning a promotion line and SHALL return its components at quantities proportional to the returned quantity.

#### Scenario: Partial promotion return

- **WHEN** a two-quantity promotion line is returned at quantity 1
- **THEN** each component is returned at half of its consumed quantity

### Requirement: Offline promotions

The system SHALL let a promotion be sold while the terminal is offline, using locally cached promotion definitions, and SHALL validate the selection again when the queued transaction syncs.

#### Scenario: Offline selection survives a later promotion edit

- **WHEN** a promotion sale is queued offline and the promotion definition changes before sync
- **THEN** the synced transaction reproduces the selection as sold, from the snapshot carried in the queued payload
- **AND** stock is deducted for those components, not for any option added to the promotion afterwards

### Requirement: Promotions without definitions are inert

The system SHALL behave exactly as before when no promotion records exist or the feature is disabled for the profile, leaving plain item selling untouched.

#### Scenario: Feature disabled

- **WHEN** promotions are disabled for a POS profile
- **THEN** no promotion appears in the catalogue and no selection dialog can be triggered
