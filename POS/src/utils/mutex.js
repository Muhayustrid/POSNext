/**
 * Mutex Utilities
 *
 * Provides mutex/lock implementations for preventing concurrent operations.
 *
 * @example
 * import { CoalescingMutex } from '@/utils/mutex'
 *
 * const mutex = new CoalescingMutex({ timeout: 30000 })
 *
 * // Multiple concurrent calls will be coalesced
 * await mutex.withLock(async () => {
 *   // Only one execution at a time
 *   await doExpensiveOperation()
 * })
 */

/**
 * Race an already-started work promise against a timeout.
 *
 * COR-FE-07: rejecting the race does NOT stop the work. The timeout is a
 * signal to the caller only; the fn keeps running and the lock stays held
 * until it settles.
 * @private
 */
function raceWithTimeout(work, timeoutMs, name) {
	let timeoutId;
	const timeout = new Promise((_, reject) => {
		timeoutId = setTimeout(() => {
			reject(new Error(`${name}: Operation timed out after ${timeoutMs}ms`));
		}, timeoutMs);
	});
	return Promise.race([work, timeout]).finally(() => clearTimeout(timeoutId));
}

/**
 * Coalescing mutex that ensures only one operation runs at a time.
 *
 * Behavior:
 * - If no operation is running, starts one immediately
 * - If an operation is running, waits for it to complete then re-checks for pending work
 * - Prevents thundering herd by coalescing concurrent requests
 * - Includes timeout protection to prevent indefinite hangs
 *
 * Use cases:
 * - Sync operations (prevent duplicate syncs)
 * - API calls that should not run concurrently
 * - Resource initialization that should happen once
 */
export class CoalescingMutex {
	/**
	 * @param {Object} options - Configuration options
	 * @param {number} options.timeout - Timeout in milliseconds (default: 60000)
	 * @param {string} options.name - Optional name for debugging
	 */
	constructor(options = {}) {
		this._activePromise = null;
		this._timeout = options.timeout ?? 60000;
		this._name = options.name || "Mutex";
	}

	/**
	 * Check if the mutex is currently locked
	 * @returns {boolean}
	 */
	get isLocked() {
		return this._activePromise !== null;
	}

	/**
	 * Execute function with exclusive access.
	 * If locked, waits for completion then re-executes to catch any new work.
	 *
	 * @param {Function} fn - Async function to execute
	 * @param {Function} logFn - Optional logging function for debug output
	 * @returns {Promise} Result of the function execution
	 */
	async withLock(fn, logFn = null) {
		// If already running, wait for it then run again to catch new work
		if (this._activePromise) {
			logFn?.(`${this._name}: Waiting for ongoing operation to complete...`);
			try {
				// Bounded wait slices: the lock promise only ever resolves, so
				// any rejection here is the slice timeout. Swallow it and
				// recurse - the next slice re-checks the lock and eventually
				// runs this caller's fn once the work settles. No single
				// eternal await, and the lock stays with the running work.
				await raceWithTimeout(
					this._activePromise,
					this._timeout,
					`${this._name} (waiting)`,
				);
			} catch {
				// slice timed out: fall through to the recursive wait below
			}
			// Recursive call - will either start fresh or wait again
			return this.withLock(fn, logFn);
		}

		// COR-FE-07: the lock is held by the WORK, not by the waiter. The
		// timeout below only abandons the caller; releasing the lock there
		// used to let a second sync loop start while this one still ran.
		// Promise.resolve().then(fn): a synchronous throw from fn becomes a
		// rejected work promise instead of an escaped synchronous error that
		// would skip the release wiring below.
		const work = Promise.resolve().then(fn);
		// If the caller already timed out, its rejection must stay handled.
		work.catch(() => {});
		let openLock;
		const lock = new Promise((resolve) => {
			openLock = resolve;
		});
		const release = () => {
			openLock();
			if (this._activePromise === lock) {
				this._activePromise = null;
			}
		};
		work.then(release, release);

		this._activePromise = lock;

		return raceWithTimeout(work, this._timeout, this._name);
	}
}

/**
 * Simple mutex that queues concurrent callers.
 * Unlike CoalescingMutex, each caller executes their own function in order.
 *
 * Use cases:
 * - Sequential database writes
 * - Operations where each call must execute independently
 */
export class QueuedMutex {
	/**
	 * @param {Object} options - Configuration options
	 * @param {number} options.timeout - Timeout in milliseconds (default: 60000)
	 * @param {string} options.name - Optional name for debugging
	 */
	constructor(options = {}) {
		this._queue = Promise.resolve();
		this._timeout = options.timeout ?? 60000;
		this._name = options.name || "QueuedMutex";
		this._pendingCount = 0;
	}

	/**
	 * Check if the mutex has pending operations
	 * @returns {boolean}
	 */
	get isLocked() {
		return this._pendingCount > 0;
	}

	/**
	 * Number of operations waiting in queue
	 * @returns {number}
	 */
	get pendingCount() {
		return this._pendingCount;
	}

	/**
	 * Execute function in queue order.
	 * Each caller waits for previous callers to complete.
	 *
	 * @param {Function} fn - Async function to execute
	 * @param {Function} logFn - Optional logging function
	 * @returns {Promise} Result of the function execution
	 */
	async withLock(fn, logFn = null) {
		this._pendingCount++;

		if (this._pendingCount > 1) {
			logFn?.(`${this._name}: Queued (${this._pendingCount - 1} ahead)`);
		}

		// COR-FE-07: the queue advances when the WORK settles, not when a
		// timed-out caller stops waiting - otherwise the next queued job
		// would overlap the one still running.
		let settleWork;
		const workDone = new Promise((resolve, reject) => {
			settleWork = { resolve, reject };
		});

		const prev = this._queue;
		const result = prev.then(() => {
			// Promise.resolve().then(fn): a synchronous throw from fn must
			// become a rejected work promise. An escaped synchronous error
			// would leave workDone forever pending and kill the whole queue.
			const work = Promise.resolve().then(fn);
			work.catch(() => {});
			work.then(settleWork.resolve, settleWork.reject);
			work.then(
				() => {
					this._pendingCount--;
				},
				() => {
					this._pendingCount--;
				},
			);
			// The timeout races the CALLER only; it starts when the job
			// reaches the head of the queue, and it never opens the queue.
			return raceWithTimeout(work, this._timeout, this._name);
		});

		this._queue = prev
			.then(() => workDone)
			.catch(() => {});
		// A rejection after the caller timed out must stay handled.
		workDone.catch(() => {});

		return result;
	}
}
