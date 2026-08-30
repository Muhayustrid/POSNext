## Purpose

Prints a submitted sale receipt onto the thermal printer attached to the cashier's own terminal, with a server-recorded job lifecycle so a failed or duplicated print is visible and recoverable.

## ADDED Requirements

### Requirement: Direct printing is opt-in per terminal

The system SHALL print directly to device hardware only when a print terminal is bound to the sale's company and POS profile and is enabled, and SHALL otherwise print through the existing browser path with no change in behaviour.

#### Scenario: No terminal bound

- **WHEN** a cashier prints a receipt on a profile with no enabled terminal
- **THEN** the browser print path is used
- **AND** no print job record is created

#### Scenario: Printer app unavailable

- **WHEN** the direct-print subsystem is not installed or reachable on the server
- **THEN** the sale is unaffected and printing degrades to the browser path with a notice to the cashier

### Requirement: Terminal resolution is scoped

The system SHALL resolve one terminal for a sale from its company and POS profile, SHALL NOT fall back to a terminal belonging to a different profile, and SHALL record on the job which terminal was chosen.

#### Scenario: Correct terminal selected

- **WHEN** two profiles each have an enabled terminal and a sale belongs to profile A
- **THEN** the print job targets profile A's terminal

#### Scenario: Ambiguous binding

- **WHEN** a sale matches more than one enabled terminal for the same profile
- **THEN** resolution fails with an actionable configuration error rather than picking one at random

### Requirement: Print job lifecycle

The system SHALL create a print job for each direct print attempt, SHALL reserve it before any hardware interaction, SHALL move it through its defined states, and SHALL record each attempt with its outcome. A job SHALL NOT be printable twice by two concurrent actors.

#### Scenario: Successful print

- **WHEN** a receipt prints successfully
- **THEN** the job reaches its completed state with an attempt record showing success
- **AND** the transaction is marked as printed

#### Scenario: Concurrent claim prevented

- **WHEN** two windows on the same terminal try to print the same receipt at once
- **THEN** one proceeds and the other is refused with an indication that the receipt is already being printed

#### Scenario: Failure is recorded, not swallowed

- **WHEN** the device reports an error during printing
- **THEN** the attempt records the failure class
- **AND** the job ends in a state from which a retry or browser fallback is offered to the cashier

### Requirement: Receipt content is fixed before printing

The system SHALL bind a printed receipt to a snapshot of its rendered content and a hash of that snapshot at the time the job is created, and SHALL print that snapshot rather than re-reading live transaction data.

#### Scenario: Edit after print does not change what printed

- **WHEN** a receipt is printed and the transaction is subsequently amended
- **THEN** the job still shows the content as printed

#### Scenario: Reprint reproduces the original

- **WHEN** a cashier reprints a previously printed transaction
- **THEN** the reprint uses the stored snapshot and its hash matches the original

### Requirement: Reprint requires a reason and is auditable

The system SHALL require a reason for a reprint, SHALL link the reprint to the original job, and SHALL allow reprinting only for transactions the operator may see and only from the terminal that printed the original or one now bound to the same profile.

#### Scenario: Reprint without reason rejected

- **WHEN** a reprint is requested with no reason
- **THEN** the request is rejected

#### Scenario: Reprint from another profile blocked

- **WHEN** a reprint of a transaction is attempted from a terminal bound to a different POS profile
- **THEN** the request is refused

### Requirement: Buyer-facing content reaches only the buyer's printer

The system SHALL send only the buyer receipt to the thermal printer and SHALL NOT print internal copies — cashier or reconciliation copies — to a device that has not been declared for them.

#### Scenario: Single customer copy

- **WHEN** a receipt prints on a terminal configured for customer copies only
- **THEN** exactly one physical copy is produced

### Requirement: Device faults are distinguishable and retryable

The system SHALL distinguish a device that is absent from one that is present but unable to print — out of paper, cover open, offline, or unqualified hardware — SHALL report which, and SHALL let the cashier retry without losing the sale.

#### Scenario: Printer not present

- **WHEN** printing is attempted and no device is reachable from the terminal
- **THEN** the failure is classified as device-absent
- **AND** browser fallback is offered

#### Scenario: Out of paper

- **WHEN** the device reports a paper or cover condition
- **THEN** the failure is classified as a device condition and the cashier is told to check the printer
- **AND** retrying after correction succeeds without a duplicate job

#### Scenario: Unqualified hardware

- **WHEN** the bound terminal's qualification status is blocked
- **THEN** direct printing is refused and the browser path is used instead

### Requirement: Sale submission never waits on the printer

The system SHALL complete and confirm the sale independently of printing. A print failure SHALL NOT roll back, block, or delay submission, and an already-submitted transaction SHALL remain printable.

#### Scenario: Printer down at checkout

- **WHEN** a cashier submits a sale while the device is unreachable
- **THEN** the sale is submitted and confirmed
- **AND** the cashier is offered retry or browser print for the receipt

### Requirement: Offline receipts print locally and reconcile later

The system SHALL print an offline sale from locally cached data without a server round trip, and SHALL record the job once connectivity returns so the audit trail shows it happened.

#### Scenario: Offline print

- **WHEN** a sale is completed offline on a terminal with an enabled device
- **THEN** the receipt prints from local data
- **AND** after reconnecting, a job record exists for it whose terminal, transaction reference, and content hash are the locally-recorded values and whose status is marked as printed while offline

### Requirement: Receipt layout matches the device width

The system SHALL render receipt content for the paper width declared on the bound terminal and SHALL NOT let content overflow the printable width.

#### Scenario: 58 millimetre device

- **WHEN** the bound terminal declares 58 millimetre paper
- **THEN** line items, totals, and any code render inside that width without truncating the amount or buyer identifier

### Requirement: Printing respects existing permissions

The system SHALL require the same permission to print or reprint a transaction that the transaction itself requires, and SHALL expose job and terminal listings only to roles permitted to configure printing.

#### Scenario: Cashier cannot configure terminals

- **WHEN** a cashier attempts to edit a print terminal or a print job record
- **THEN** the action is refused

#### Scenario: Device diagnostics are limited

- **WHEN** a user with print access but not configuration access views a job
- **THEN** raw device diagnostics and internal reservation data are withheld
