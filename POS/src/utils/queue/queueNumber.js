/**
 * Daily per-outlet queue numbers.
 *
 * Online: the server allocates under a row lock (unique across tills).
 * Offline (or when the API is unreachable): continue locally from the last
 * number this device printed for the company today — two tills offline at
 * the same moment can collide; that trade-off is accepted (queue numbers
 * are a calling aid, not a legal document).
 *
 * The last number per company lives in localStorage as
 * `pos_queue_last::<company>` = `{date, number}`; it is rewritten on every
 * allocation so the next sale (online or off) always continues after it.
 */
import { call } from "@/utils/apiWrapper"
import { logger } from "@/utils/logger"

const log = logger.create("QueueNumber")
const cacheKey = (company) => `pos_queue_last::${company}`
const localDate = () => new Date().toISOString().slice(0, 10)

/** The cached `{date, number}` for a company, or null when absent/corrupt. */
export function readQueueCache(company) {
	try {
		const raw = JSON.parse(localStorage.getItem(cacheKey(company)) || "null")
		if (raw && typeof raw.number === "number" && typeof raw.date === "string") {
			return raw
		}
	} catch {
		/* corrupt cache = absent */
	}
	return null
}

function writeQueueCache(company, date, number) {
	localStorage.setItem(cacheKey(company), JSON.stringify({ date, number }))
}

/** "048" for the counter display; plain beyond 999, "" for anything not a positive number. */
export function formatQueueNumber(n) {
	const v = Number(n)
	if (!Number.isFinite(v) || v <= 0) return ""
	return String(Math.floor(v)).padStart(3, "0")
}

async function fetchServerNumber(posProfile) {
	const res = await call("pos_next.api.queue.get_next_queue_number", {
		pos_profile: posProfile,
	})
	if (!res?.enabled) return null
	return { queue_number: res.queue_number, date: res.date }
}

/** Local continuation: +1 from today's cache, restart at 1 when stale/absent. */
function nextLocalNumber(company) {
	const today = localDate()
	const cached = readQueueCache(company)
	const number = cached && cached.date === today ? cached.number + 1 : 1
	return { queue_number: number, date: today }
}

/**
 * Allocate today's queue number for a sale. Returns
 * `{queue_number, date}` or null when the company disabled the queue.
 * A fetch failure degrades to the offline path rather than losing the number.
 */
export async function acquireQueueNumber({ company, posProfile, offline = false }) {
	if (!company) return null
	if (!offline) {
		try {
			const out = await fetchServerNumber(posProfile)
			if (out) {
				writeQueueCache(company, out.date, out.queue_number)
				return out
			}
			return null // disabled company — no cache write
		} catch (err) {
			log.warn("queue number fetch failed, continuing locally:", err?.message || err)
		}
	}
	const out = nextLocalNumber(company)
	writeQueueCache(company, out.date, out.queue_number)
	return out
}
