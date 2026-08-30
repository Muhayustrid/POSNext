## Purpose

Controls which parts of a cart a coupon may discount — the whole transaction, or only named items, groups, or brands — and who may redeem it.

## ADDED Requirements

### Requirement: Coupon discount scope

The system SHALL let a coupon declare what it discounts: the whole transaction, or only the lines matching a scope of item codes, item groups, or brands. A coupon with a scoped discount SHALL compute its discount only from the value of matching lines and SHALL NOT reduce the value of non-matching lines.

#### Scenario: Coupon restricted to one item

- **WHEN** a 20 percent coupon is scoped to the ropi coklat item and the cart holds ropi coklat worth 10,000 and a drink worth 20,000
- **THEN** the discount is 2,000
- **AND** the drink line keeps its full price

#### Scenario: Coupon scoped to an item group

- **WHEN** a fixed-amount coupon is scoped to the "Bread" item group and the cart contains two Bread lines and one Beverage line
- **THEN** only the Bread lines contribute to the discount base

#### Scenario: Scope with no matching lines

- **WHEN** a scoped coupon is applied to a cart containing no matching item
- **THEN** the coupon is reported as not applicable to this cart
- **AND** the discount applied is zero

#### Scenario: Unrestricted coupon still discounts the whole cart

- **WHEN** a coupon carries no scope
- **THEN** it behaves as it does today, discounting from the transaction total

### Requirement: Discounted scope survives cart edits

The system SHALL recompute a scoped coupon's discount whenever the cart changes, and SHALL drop the coupon's effect entirely if the cart no longer qualifies.

#### Scenario: Removing the discounted item

- **WHEN** a scoped coupon is applied and the cashier removes every matching line
- **THEN** the coupon's discount is removed from the totals
- **AND** the cashier is told the coupon no longer applies

#### Scenario: Quantity change recalculates

- **WHEN** a percentage coupon is scoped to an item and that item's quantity doubles in the cart
- **THEN** the discount reflects the new value of the matching lines

### Requirement: Minimum and maximum spend are evaluated against the scope

The system SHALL evaluate a coupon's minimum-purchase requirement against the discounted value of the lines inside its scope, and SHALL cap the resulting discount at the coupon's maximum discount amount.

#### Scenario: Minimum not met inside scope

- **WHEN** a coupon requires a minimum of 15,000 within its item scope and the matching lines total 10,000
- **THEN** the coupon is rejected with the shortfall reported

#### Scenario: Cap applied

- **WHEN** a 30 percent coupon scoped to an item group yields 9,000 on matching lines and the coupon's maximum discount is 5,000
- **THEN** the applied discount is 5,000

### Requirement: Free-item coupons honour the scope

The system SHALL restrict a coupon that grants a free item to that item's own eligibility, and where the coupon is scoped, SHALL require a qualifying line in scope before granting it.

#### Scenario: Buy scoped item, get free item

- **WHEN** a coupon grants one free drink when a Bread-scoped item worth at least its minimum is in the cart
- **THEN** the free drink is added at zero price
- **AND** the free drink does not itself count toward the qualifying amount

### Requirement: Coupons usable by walk-in buyers

The system SHALL allow a coupon to be redeemed on a transaction booked against a default walk-in customer, and SHALL NOT require a named customer unless the coupon itself is restricted to one.

#### Scenario: Walk-in redeems a promotional coupon

- **WHEN** a cashier enters a promotional coupon code on a sale with no customer selected
- **THEN** the coupon validates and applies

#### Scenario: Customer-bound coupon still restricted

- **WHEN** a coupon is bound to a specific customer and is entered on a sale for a different customer or on a walk-in sale
- **THEN** the coupon is rejected as not valid for this customer

#### Scenario: Gift card remains single-use

- **WHEN** a gift-card coupon that has already been used is entered again
- **THEN** it is rejected as already used

### Requirement: Scoping does not change stacking rules

The system SHALL keep the existing rules for combining coupons with each other and with automatic offers. A coupon's scope SHALL limit only which lines its discount is computed from; it SHALL NOT make a coupon that is currently blocked from combining become allowed, or the reverse.

#### Scenario: Coupon that cannot stack still cannot stack

- **WHEN** a scoped coupon is entered on a cart where an automatic offer is already applied to a matching line, and that offer does not stack with coupons under the current rules
- **THEN** the coupon is not additionally applied to that line

#### Scenario: Discount base shrinks, not the combination rules

- **WHEN** the same coupon is evaluated with and without a scope
- **THEN** the set of promotions that may coexist with it is the same in both cases
- **AND** only the amount of the coupon's own discount differs

### Requirement: Coupon usage is recorded per scope

The system SHALL record, for each redeemed coupon, the amount discounted and the lines it affected, so usage reports can distinguish which items a coupon drove.

#### Scenario: Redemption audit

- **WHEN** a scoped coupon is applied to a submitted transaction
- **THEN** the coupon usage record lists the discounted amount and the affected lines

#### Scenario: Usage limit counts transactions

- **WHEN** a coupon has a maximum use of one and is redeemed on a transaction
- **THEN** a second redemption on a later transaction is rejected

### Requirement: Scopes are additive configuration

The system SHALL treat existing coupons as unrestricted, and SHALL keep validating, applying, and reporting them exactly as before for any coupon that has no scope set.

#### Scenario: Legacy coupon unaffected

- **WHEN** a coupon created before this change is redeemed
- **THEN** its discount is computed on the transaction total as it is today
