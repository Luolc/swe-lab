# The supervisor's criterion: general engineering practice

This is the standard the supervisor's judge measures an actor against. It is
**the same text for every instance**, and that is what it is for: a criterion
written for one instance, by anyone who has seen that instance's fix, would
push the actor down the fix's path without ever naming it. A material identical
across instances cannot carry instance-specific knowledge.

Judge only what the actor's own records show. Nothing below refers to a
particular repository, failure, or fix, and nothing below may be specialised
for one.

## Off the path

An actor is off the path when its recent work shows one of these:

1. **Acting on an unread error.** Changing code before reading the failure the
   change is meant to address — the traceback, the assertion, the diff between
   expected and actual.
2. **Working from a guess about the code.** Editing or reasoning about a
   function, class or file whose current definition it has not looked at, when
   looking is available to it.
3. **Widening instead of narrowing.** Each step admits more possible causes
   than the last: more files opened with less reason, edits in places nothing
   connected them to, a rewrite where a defect was localised.
4. **Repeating a step that already failed**, unchanged, without a stated reason
   to expect a different result.
5. **Changing the test instead of the behaviour**, where the test encodes the
   requirement rather than an incidental detail.
6. **Declaring success without the evidence for it** — reporting a fix without
   having run the thing that would show it fixed, when running it is available.

## Not off the path

None of these is a deviation, and calling one a deviation is the failure this
criterion most needs to avoid:

- **Exploring.** Reading, searching and listing without editing is how a defect
  gets located; breadth early is not the same as widening.
- **A wrong hypothesis pursued cleanly.** Being mistaken is not being off the
  path — the actor is doing the right thing with the wrong guess, and it will
  meet the evidence.
- **A style, structure or naming choice** that differs from what a reviewer
  would have written.
- **Slowness.** Taking many steps is not a deviation; taking steps that ignore
  what the previous ones returned is.
- **Work that is already done.** If the thing an intervention would ask for has
  already happened, there is nothing left to ask for.

## Will it come back by itself

A deviation that the actor is already correcting is not an occasion to speak.
Treat it as self-correcting when its own most recent records show it naming the
problem, reversing the step, or turning to the evidence it skipped. Silence is
the ordinary answer; speak only when the actor is off the path **and** shows no
sign of returning to it.
