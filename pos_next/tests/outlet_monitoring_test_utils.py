# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Exact preservation of the POS Monitoring Settings single for tests.

Every monitoring test module used to snapshot `POS Monitoring Settings` with
`get_singles_dict` plus a partial `get_all` of the child table (name/label/idx)
and "restore" it by deleting and re-inserting rows from that partial dict. That
lossily rewrote metadata and any custom column, and changed `creation` /
`modified` of rows the test never intended to touch.

These helpers snapshot the FULL raw rows (`select *`) and restore them exactly:
same child row identities, same every-column values, same `tabSingles`
presence/value. Nothing is synthesised and no row is recreated unless it is
missing.
"""

import frappe

CHILD_DOCTYPE = "POS Monitoring Label Item"
SETTINGS = "POS Monitoring Settings"


def snapshot_settings_state() -> dict:
	"""Full raw rows: every column of the Settings children and tabSingles."""
	return {
		"children": frappe.db.sql(
			f"select * from `tab{CHILD_DOCTYPE}` where parenttype = %s",
			(SETTINGS,),
			as_dict=True,
		),
		"singles": frappe.db.sql(
			"select field, value from tabSingles where doctype = %s",
			(SETTINGS,),
			as_dict=True,
		),
	}


def _upsert(table: str, row: dict) -> None:
	cols = list(row)
	col_sql = ", ".join(f"`{c}`" for c in cols)
	placeholders = ", ".join(["%s"] * len(cols))
	updates = ", ".join(f"`{c}` = values(`{c}`)" for c in cols if c != "name")
	if updates:
		frappe.db.sql(
			f"insert into `tab{table}` ({col_sql}) values ({placeholders}) on duplicate key update {updates}",
			tuple(row[c] for c in cols),
		)
	else:
		frappe.db.sql(
			f"insert ignore into `tab{table}` ({col_sql}) values ({placeholders})",
			tuple(row[c] for c in cols),
		)


def restore_settings_state(state: dict) -> None:
	"""Restore a `snapshot_settings_state()` snapshot exactly.

	Child rows are upserted with every column from the snapshot (so `creation`,
	`modified`, `owner`, `docstatus` and any custom column come back as they
	were) and rows that did not exist in the snapshot are removed. `tabSingles`
	is rebuilt to the exact snapshot rows because the table has no unique key to
	upsert on.
	"""
	frappe.set_user("Administrator")
	children = state.get("children") or []
	names = [row["name"] for row in children]
	if names:
		placeholders = ", ".join(["%s"] * len(names))
		frappe.db.sql(
			f"delete from `tab{CHILD_DOCTYPE}` where parenttype = %s and name not in ({placeholders})",
			(SETTINGS, *names),
		)
	else:
		frappe.db.sql(f"delete from `tab{CHILD_DOCTYPE}` where parenttype = %s", (SETTINGS,))
	for row in children:
		_upsert(CHILD_DOCTYPE, row)

	frappe.db.sql("delete from tabSingles where doctype = %s", (SETTINGS,))
	for row in state.get("singles") or []:
		frappe.db.sql(
			"insert into tabSingles (doctype, field, value) values (%s, %s, %s)",
			(SETTINGS, row["field"], row["value"]),
		)
	frappe.clear_document_cache(SETTINGS, SETTINGS)
	# Fail loudly here rather than leaving a silently lossy cleanup behind.
	assert_settings_state_equal(state, "restore_settings_state")


def owned_fixture_names(doctype, current_names, initial_names, prefixes, run_id) -> set[str]:
	"""Snapshot diff narrowed to names this run can have created.

	A plain diff is not ownership: a row another writer added after the
	snapshot lands in it too, and deleting it would destroy their work. Every
	prefix must therefore carry THIS run's token, and a name is only claimed
	when it starts with such a prefix -- a foreign row that merely shares the
	suite's namespace (`API Category <other-run>`) never matches. A prefix
	without the token is a bug, not a wildcard: it raises instead of silently
	claiming everything under it. An unlisted doctype matches nothing, i.e. it
	fails closed and leaks rather than deleting blindly.
	"""
	allowed = []
	for prefix in prefixes.get(doctype, ()):
		if not prefix or run_id not in prefix:
			raise AssertionError(f"cleanup prefix {prefix!r} for {doctype} lacks this run's token {run_id!r}")
		allowed.append(prefix)
	if not allowed:
		return set()
	new_names = set(current_names) - set(initial_names)
	return {name for name in new_names if name.startswith(tuple(allowed))}


def linked_fixture_names(doctype, field, parent_names) -> set[str]:
	"""Rows of `doctype` whose `field` points at a parent this suite owns."""
	if not parent_names:
		return set()
	return set(frappe.get_all(doctype, filters={field: ["in", list(parent_names)]}, pluck="name"))


def owned_file_names(report_names) -> set[str]:
	"""Files attached to reports this suite owns; no report, no file claim."""
	if not report_names:
		return set()
	return set(
		frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": "POS Monitoring Report",
				"attached_to_name": ["in", list(report_names)],
			},
			pluck="name",
		)
	)


def delete_owned_files(names) -> None:
	"""Delete File rows through `delete_doc` so the blob lifecycle still runs."""
	frappe.set_user("Administrator")
	for name in names or ():
		try:
			frappe.flags.internal_monitoring_delete = True
			frappe.delete_doc("File", name, force=True, ignore_permissions=True)
		except Exception:
			pass
		finally:
			frappe.flags.internal_monitoring_delete = False


def owned_fixtures(
	initial: dict, prefixes: dict, links: dict, run_id: str, anchors: dict | None = None
) -> dict:
	"""Rows created after `initial` that this run can prove it owns.

	`prefixes` maps a doctype to the name prefixes its fixtures use; every
	prefix carries `run_id` so no other run's row can match. `links` maps a
	doctype to `((field, parent_doctype), ...)`; such a row is claimed only
	through a parent already claimed. `anchors` names suite-created parents
	that must survive a per-test cleanup (a class-level template): they
	resolve links but are never returned for deletion. A doctype in none of
	the three claims nothing, so the cleanup leaks a fixture rather than
	deleting a row another writer created.
	"""
	owned = {
		dt: owned_fixture_names(
			dt, frappe.get_all(dt, pluck="name"), initial.get(dt, set()), prefixes, run_id
		)
		for dt in prefixes
	}
	parents = dict(owned)
	for dt, names in (anchors or {}).items():
		parents[dt] = parents.get(dt, set()) | set(names)
	for dt, field_pairs in links.items():
		owned[dt] = set()
		for field, parent in field_pairs:
			owned[dt] |= linked_fixture_names(dt, field, parents.get(parent, set()))
	return owned


_USER_INSERTS = {}


def _user_dependents(email):
	contacts = set(frappe.get_all("Contact", filters={"user": email}, pluck="name"))
	contacts.update(frappe.get_all("Contact Email", filters={"email_id": email}, pluck="parent"))
	rows = {"Contact": contacts}
	for df in frappe.get_meta("Contact").get_table_fields():
		rows[df.options] = linked_fixture_names(df.options, "parent", contacts)
	for df in frappe.get_meta("User").get_table_fields():
		rows[df.options] = set(
			frappe.get_all(df.options, filters={"parent": email, "parenttype": "User"}, pluck="name")
		)
	rows["DefaultValue"] = set(frappe.get_all("DefaultValue", filters={"parent": email}, pluck="name"))
	return rows


def insert_owned_user(user, run_id, role):
	"""Register only successful, run-owned User inserts and their new native dependents."""
	from frappe.contacts.doctype.contact.contact import get_contact_name

	user.email = user.email.strip().lower()
	if not run_id or run_id not in user.email:
		raise AssertionError("User fixture must contain its current run token")
	if get_contact_name(user.email):
		raise AssertionError("User fixture email already belongs to an existing Contact")
	before = _user_dependents(user.email)
	# A Contact may gain the User link during insertion; existence alone is never ownership.
	existing_contacts = set(frappe.get_all("Contact", pluck="name"))
	was_in_test = frappe.in_test
	try:
		# Native User.on_update otherwise queues Contact creation after our ownership capture.
		frappe.in_test = True
		user.insert(ignore_permissions=True)
	finally:
		frappe.in_test = was_in_test
	after = _user_dependents(user.name)
	new_contacts = after["Contact"] - existing_contacts
	owned = {dt: names - before.get(dt, set()) for dt, names in after.items()}
	owned["Contact"] = new_contacts
	for df in frappe.get_meta("Contact").get_table_fields():
		owned[df.options] &= linked_fixture_names(df.options, "parent", new_contacts)
	_USER_INSERTS[user.name] = owned
	row = frappe.get_doc(
		{
			"doctype": "Has Role",
			"parent": user.name,
			"parenttype": "User",
			"parentfield": "roles",
			"role": role,
		}
	).insert(ignore_permissions=True)
	owned.setdefault("Has Role", set()).add(row.name)
	return user


def delete_owned_fixtures(owned: dict, children: dict, order) -> None:
	"""Delete owned rows innermost-children-first, in the given doctype order."""
	frappe.set_user("Administrator")
	for dt in order:
		names = owned.get(dt)
		if not names:
			continue
		if dt == "User":
			for name in names:
				for dependent, identities in _USER_INSERTS.pop(name, {}).items():
					if identities:
						frappe.db.delete(dependent, {"name": ["in", list(identities)]})
				frappe.clear_cache(user=name)
			frappe.db.delete(dt, {"name": ["in", list(names)]})
			continue
		for child in children.get(dt, ()):
			if child == "POS Monitoring Label Item":
				frappe.db.delete(child, {"parenttype": dt, "parent": ["in", list(names)]})
			elif child == "Has Role":
				frappe.db.delete(child, {"parenttype": "User", "parent": ["in", list(names)]})
			else:
				frappe.db.delete(child, {"parent": ["in", list(names)]})
		if dt == "POS Monitoring Label":
			for name in names:
				frappe.db.delete("POS Monitoring Label Item", {"label": name})
		frappe.db.delete(dt, {"name": ["in", list(names)]})


def assert_settings_state_equal(state: dict, message: str = "") -> None:
	"""Compare a snapshot with the current DB state, ignoring row order only."""
	current = snapshot_settings_state()
	for key in ("children", "singles"):
		left = sorted(tuple(sorted(r.items(), key=lambda kv: kv[0])) for r in state[key])
		right = sorted(tuple(sorted(r.items(), key=lambda kv: kv[0])) for r in current[key])
		if left != right:
			raise AssertionError(f"{message}: {key} differ\nbefore={left}\nafter={right}")
