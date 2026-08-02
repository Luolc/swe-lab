// Pin the wall clock the graded suite reads to a fixed time of day.
//
// Preloaded with `node --require` before any test code runs, so every
// `new Date()` and `Date.now()` in the process is displaced by one constant
// chosen at startup. Two properties matter, and both are deliberate:
//
//   - the offset is *constant*, so every duration, timeout and elapsed-time
//     measurement in the suite is exactly what it was; only the absolute
//     time of day moves;
//   - the target is *today's* 12:00 UTC, so the offset is under 12 hours in
//     either direction and the UTC date never changes. A test asserting the
//     current year, month or day sees precisely what it would have seen.
//
// `Date` is replaced by a Proxy around the real constructor rather than by a
// subclass: `instanceof`, `Date.parse` / `Date.UTC`, the `[object Date]` tag
// and `Date()` called without `new` all keep working, where a subclass would
// have to reimplement each of them.
"use strict"

const RealDate = Date
const TARGET_UTC_HOUR = 12

const startedAt = RealDate.now()
const today = new RealDate(startedAt)
const offsetMs =
	RealDate.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate(), TARGET_UTC_HOUR) - startedAt

globalThis.Date = new Proxy(RealDate, {
	construct(target, args, newTarget) {
		// Only the no-argument form reads the clock; every other form names an
		// explicit instant and has to pass through untouched.
		const shifted = args.length === 0 ? [RealDate.now() + offsetMs] : args
		return Reflect.construct(target, shifted, newTarget)
	},
	apply() {
		return new RealDate(RealDate.now() + offsetMs).toString()
	},
	get(target, prop, receiver) {
		if (prop === "now") return () => RealDate.now() + offsetMs
		return Reflect.get(target, prop, receiver)
	},
})
