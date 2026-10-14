# Review of ntsb_qa_semantic.json (NS01, NS02)

Reviewer: Claude (Fable 5.1), independent of the writer. Read: all 31 chunks of chunks.json, the staging tables, earlier_questions.json and the four earlier gold files. Nothing from out/, mlruns, docs or src was opened.

Method: re-derived each answer set from every chunk (not only the cited ones), then ran a script that (a) checks each quote is a verbatim substring of its chunk and (b) scans all chunks for idea words (restart, attempt, tried, unsuccessful, regain, restore, noise, sound, heard, pop, cough, bang, rough, shake, horn, loud). Every stray hit was a substring of another word ("through", "cloud", "windshear"); no uncited qualifying passage exists for either question.

## NS01 (restart attempts that failed)

Checked:
- Answer set re-derived: CEN21LA414, CEN22LA194, CEN22LA059, CEN23LA344. All 14 power-loss narratives were read; only these four describe an in-flight attempt to get the engine going again, and all four attempts failed. All four are total losses of engine power.
- Quotes: all four verbatim; one chunk per accident (CEN23LA344's probable cause is a separate chunk, #5, and does not express the attempt, so it is rightly not cited).
- Worded differently: the question's idea words (tried / running again / could not) appear in none of the four passages, which say "attempted to restart ... with no success", "multiple attempts to restart ... unsuccessful", "attempts to restore fuel flow and restart ... unsuccessful", and (CEN23LA344, never naming a restart) "attempted to restore engine power by switching to the right fuel tank and activating the starter but was unsuccessful". The noun "engine" is shared by every passage; it names the thing restarted, not the idea, and I found no natural wording that avoids it. Recorded in the note.
- Lookalikes are real: CEN21LA362 (engine kept running, pilot advanced the throttle), CEN21LA173 (rotor rpm, not the engine), CEN21LA383 (power came back briefly on its own, no attempt stated), post-accident test runs of CEN21LA382 and CEN22LA059 (on the ground, and they succeeded).
- Novelty: NE03, NE17, NC14 and ND07 touch these narratives but ask other facts (a medical certificate, the engine's response to throttle, flight hours, post-accident test runs). No earlier question asks for the set of failed restart attempts.

Changed:
- "after it lost power in flight" -> "after it stopped producing power in flight". "Lost power" also covers partial losses, which let a reader argue CEN21LA362 ("tried to advance the power forward with no change") or CEN21LA383 (engine went to idle) in. "Stopped producing power" matches the four members (all coded as total losses) and shuts those two out, as well as CEN23LA223 ("lost thrust, but the engine remained operating").
- Note extended: states why the wording changed, adds CEN23LA223 as a named lookalike, and records the shared noun "engine".

Doubts left standing:
- CEN21LA414's narrative never says the engine stopped; it says the engine "did not respond when power was added" and that the instructor "attempted to restart" it. The examination section calls it a "total loss of engine power", so membership is clear in context, but a reader working from the first paragraph alone has to infer the stop from the word "restart".

## NS02 (unusual noise the pilot heard)

Checked:
- Answer set re-derived: CEN21LA268, CEN22LA312, CEN22LA244, CEN23LA017. Every narrative was read for anything heard: the only other hearing events are a warning horn (ANC21LA079, CEN21LA185), a stall warning (CEN22LA249), witnesses hearing a go-around (CEN21FA299), and CEN22LA310 (normal spool-up, then the wing hitting the corn, which is the accident itself).
- The writer's flagged risk, CEN21LA383 "engine ran rough": the narrative describes a behaviour of the engine, never a noise or anything heard. Under the original wording ("heard an unusual noise") it already did not count, but the new wording makes it explicit that a noise must be described. I deliberately did not add "rough running does not count" to the question, because CEN23LA017's "coughing" engine could then be read as rough running and wrongly dropped.
- Quotes: all four verbatim, including the curly quotes around "very loud pop", "pop" and "coughing".
- Worded differently: this was the real weakness. The original question said "from the airplane or its engine", so the passage the writer offered as the fully different one (CEN23LA017, "the engine made a coughing sound and lost power") shared the key word "engine", and two others share "airplane". After the change, CEN23LA017 shares no word with the question.
- Lookalikes: the warning horns were the weak spot. The original wording relied on a reader agreeing that a horn is not an "unusual noise"; a careful reader could count ANC21LA079 ("the horn sounded") or CEN21LA185 ("the stall warning horn came on"). Now excluded by name in the question.
- Novelty: ND02 (slight noise while taxiing) and NC17 (coughing sound) use these passages to identify one accident each and ask other facts; NB03 uses the white smoke. No earlier question asks for the set of noises.

Changed:
- Question rewritten: "In which accidents does the narrative describe an unusual noise that the pilot heard from the aircraft before the accident (a warning horn does not count)?" Three reasons: "aircraft" replaces "airplane or its engine" so that CEN23LA017 shares no key word; "describe an unusual noise" makes the rough-running and shaking cases (CEN21LA383, CEN21LA221) clearly out, since neither narrative describes a noise; the parenthesis settles the horn cases.
- Note extended: names the horn lookalikes (ANC21LA079, CEN21LA185, CEN22LA249) and explains the "aircraft" wording and the CEN21LA268 point below.

Doubts left standing:
- CEN21LA268 never uses the verb "heard": the pilot "reported that ... a 'very loud pop' noise emitted from behind the instrument panel". By meaning the pilot heard it (he reported it himself and reacted to it), and the noise is described, so it counts; a very literal reader of "the pilot heard" might hesitate.
- A reader might argue CEN21LA221's shaking engine must have been audible; the narrative does not say so, and the question asks what the narrative describes.

## Entity names
Both answers use the NTSB accident numbers exactly as the sources and the earlier gold do (empty aliases, the convention of ntsb_qa_bench.json and the confirm sets). No aliases added: the accident numbers are the only identifiers an answer is expected to give.
