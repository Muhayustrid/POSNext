def _apply_workspace_sidebar_hotfix():
	"""frappe v16.36: WorkspaceSidebar.get_can_read_items forgets to return, so
	get_cached() stores None and every DocType/Report link drops out of the
	sidebar for non-Administrator users (POS Invoice, etc.). Guarded no-op once
	frappe drops the buggy method (develop already rewrote this class)."""
	try:
		from frappe.desk.doctype.workspace_sidebar.workspace_sidebar import WorkspaceSidebar
	except ImportError:
		return
	if "get_can_read_items" not in vars(WorkspaceSidebar):
		return

	def get_can_read_items(self):
		if not self.user.can_read:
			self.user.build_permissions()
		return self.user.can_read

	WorkspaceSidebar.get_can_read_items = get_can_read_items


_apply_workspace_sidebar_hotfix()
