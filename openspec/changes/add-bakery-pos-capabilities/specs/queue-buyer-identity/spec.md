## Purpose

Lets a cashier record who a sale belongs to by name, and hand that buyer a queue number that can be called out, without turning every walk-in into a Customer master record.

## ADDED Requirements

### Requirement: Buyer name on a sale

The system SHALL let the cashier record a free-text buyer name on a sales transaction. Recording a buyer name SHALL NOT create, look up, or require a `Customer` record, and SHALL NOT change which customer the transaction is booked against. This follows the walk-in pattern already proven in the source app: the transaction's customer stays the POS Profile's default walk-in customer.

#### Scenario: Name captured without a Customer record

- **WHEN** a cashier types "Budi" as the buyer name on a walk-in sale and submits it
- **THEN** the submitted transaction stores the buyer name "Budi"
- **AND** the total count of `Customer` records is unchanged
- **AND** the transaction's customer remains the profile's default walk-in customer

#### Scenario: Name-only sale keeps walk-in accounting

- **WHEN** a sale has a buyer name but no selected customer
- **THEN** the transaction is booked against the default walk-in customer
- **AND** loyalty, credit, and outstanding-balance behaviour for the transaction is identical to a sale with no buyer name at all

#### Scenario: Buyer name only applies to the walk-in default

- **WHEN** a buyer name is submitted on a transaction whose customer is not the profile's default walk-in customer
- **THEN** the name is rejected as not applicable, exactly as the source pattern enforces

#### Scenario: Draft keeps the buyer name

- **WHEN** a cashier saves a held order containing a buyer name and later resumes it on the same or a different device
- **THEN** the resumed cart still shows the entered buyer name

### Requirement: Buyer name validation

The system SHALL reject buyer names that are empty after trimming, longer than 60 characters, or contain control characters. The system SHALL treat the buyer name as non-required unless POS Settings enables it as mandatory.

#### Scenario: Oversized name rejected

- **WHEN** a cashier submits a buyer name longer than 60 characters
- **THEN** the submission is rejected with a validation message naming the character limit
- **AND** no draft or submitted transaction is written

#### Scenario: Mandatory when configured

- **WHEN** POS Settings for the profile has buyer name required enabled and a cashier submits without one
- **THEN** the submission is rejected and the buyer name field is focused

#### Scenario: Whitespace-only name is treated as absent

- **WHEN** a cashier submits a buyer name consisting only of spaces
- **THEN** the transaction stores no buyer name rather than a blank string

### Requirement: Queue number allocation

The system SHALL assign each submitted transaction an integer queue number that is unique and sequential within its POS Opening Shift, starting at 1, and SHALL surface it on the transaction, the cart, and the printed receipt. The counter SHALL reset when a new shift opens.

#### Scenario: Sequential within a shift

- **WHEN** three sales are submitted in the same open shift
- **THEN** their queue numbers are 1, 2, and 3 in submission order

#### Scenario: Resets on new shift

- **WHEN** a cashier closes the current shift and opens a new one, then submits a sale
- **THEN** that sale's queue number is 1

#### Scenario: No gaps or duplicates under concurrent terminals

- **WHEN** two terminals on the same shift submit sales in the same second
- **THEN** both transactions receive distinct queue numbers
- **AND** no number in the sequence is skipped

#### Scenario: Offline sale receives its number at sync time

- **WHEN** a sale is created offline and later synced to the server
- **THEN** the synced transaction receives a queue number from the server-side counter for its shift
- **AND** the receipt printed at the terminal may show a locally-estimated number that is reconciled to the server value

### Requirement: Most-recent queue number for calling

The system SHALL expose the highest queue number allocated for a given shift so that a service counter can ask for the latest one, and SHALL let staff find a transaction by buyer name or queue number.

#### Scenario: Latest number for a shift

- **WHEN** a staff member requests the current queue number for the open shift
- **THEN** the system returns the highest allocated number for that shift

#### Scenario: Search by buyer name or queue number

- **WHEN** a staff member searches the invoice list for "Budi" or for "17"
- **THEN** transactions matching on buyer name or queue number are returned

#### Scenario: Unknown queue number

- **WHEN** a search for a queue number matches nothing in the current shift
- **THEN** the system reports no match instead of returning an unrelated transaction

### Requirement: Buyer name is opt-in per profile

The system SHALL keep the buyer name and queue number hidden when the feature is disabled for the POS profile, and SHALL behave exactly as it does today: no new field is shown, sent, or required.

#### Scenario: Disabled profile is unchanged

- **WHEN** buyer identity is disabled in POS Settings for a profile
- **THEN** the cart, payment screen, and invoice list show no buyer name or queue number
- **AND** submitted transactions leave those fields empty

### Requirement: Buyer name is personal data

The system SHALL store the buyer name only on the sales transaction, SHALL NOT copy it into the Customer master, and SHALL include it in any deletion or anonymisation of the transaction.

#### Scenario: Buyer name removed with the transaction

- **WHEN** a transaction is deleted or anonymised under a data-retention action
- **THEN** the stored buyer name is deleted or replaced alongside the rest of the transaction

#### Scenario: Receipt shows only what the buyer gave

- **WHEN** a receipt prints for a sale with a buyer name
- **THEN** the printed buyer name equals the stored value and nothing else about the buyer is printed
