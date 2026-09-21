// Entry for `node --test pos_next/tests/js/` on Node versions that spawn the
// directory as a module instead of expanding it (MODULE_NOT_FOUND otherwise).
// Plain `node` executes the registered node:test suites and exits nonzero on
// failure, so the runner's verdict stays a real pass/fail. Files matching
// *.test.* are not double-run: directory expansion skips this non-matching
// name.
require("./hq_highlight_names.test.cjs");
require("./hq_monitoring_utils.test.cjs");
require("./hq_ux_rules.test.cjs");
